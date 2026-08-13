"""Cache protocols, in-memory stores, and fail-open lookup coordination."""

from __future__ import annotations

import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.cache.models import (
    CacheFailure,
    CacheLookup,
    CachePolicyDecision,
    CacheWriteResult,
    QueryFingerprint,
    SemanticCacheHit,
)
from app.observability import observe_span


class ExactCacheStore(Protocol):
    """Minimal Redis-compatible exact-cache behavior."""

    async def get(self, key: str) -> dict[str, object] | None: ...

    async def set(
        self,
        key: str,
        payload: dict[str, object],
        ttl_seconds: int,
    ) -> None: ...


class SemanticCacheStore(Protocol):
    """Minimal pgvector-compatible semantic-cache behavior."""

    async def lookup(
        self,
        *,
        namespace: str,
        constraints: dict[str, object],
        embedding: tuple[float, ...],
        threshold: float,
        now: datetime,
    ) -> SemanticCacheHit | None: ...

    async def put(
        self,
        *,
        namespace: str,
        query_hash: str,
        normalized_query: str,
        constraints: dict[str, object],
        embedding: tuple[float, ...],
        payload: dict[str, object],
        expires_at: datetime,
        source_retrieved_at: datetime | None,
    ) -> None: ...


class Embedder(Protocol):
    """Separate query/document methods supported by production embedders."""

    async def embed_query(self, text: str) -> tuple[float, ...]: ...

    async def embed_document(self, text: str) -> tuple[float, ...]: ...


@dataclass(slots=True)
class _ExactEntry:
    payload: dict[str, object]
    expires_at: datetime


class InMemoryExactCache:
    """Deterministic exact cache used by unit tests and local experiments."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._entries: dict[str, _ExactEntry] = {}

    async def get(self, key: str) -> dict[str, object] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= _utc(self._clock()):
            self._entries.pop(key, None)
            return None
        return deepcopy(entry.payload)

    async def set(
        self,
        key: str,
        payload: dict[str, object],
        ttl_seconds: int,
    ) -> None:
        if ttl_seconds <= 0:
            return
        self._entries[key] = _ExactEntry(
            payload=deepcopy(payload),
            expires_at=_utc(self._clock()) + timedelta(seconds=ttl_seconds),
        )


@dataclass(slots=True)
class _SemanticEntry:
    namespace: str
    query_hash: str
    normalized_query: str
    constraints: dict[str, object]
    embedding: tuple[float, ...]
    payload: dict[str, object]
    expires_at: datetime
    source_retrieved_at: datetime | None


class InMemorySemanticCache:
    """Constraint-filtered cosine store mirroring the pgvector contract."""

    def __init__(self) -> None:
        self._entries: list[_SemanticEntry] = []

    async def lookup(
        self,
        *,
        namespace: str,
        constraints: dict[str, object],
        embedding: tuple[float, ...],
        threshold: float,
        now: datetime,
    ) -> SemanticCacheHit | None:
        current = _utc(now)
        best: tuple[float, _SemanticEntry] | None = None
        for entry in self._entries:
            if (
                entry.namespace != namespace
                or entry.constraints != constraints
                or entry.expires_at <= current
            ):
                continue
            similarity = _cosine_similarity(embedding, entry.embedding)
            if similarity < threshold:
                continue
            if best is None or similarity > best[0]:
                best = (similarity, entry)
        if best is None:
            return None
        similarity, entry = best
        return SemanticCacheHit(
            payload=deepcopy(entry.payload),
            similarity=similarity,
            query_hash=entry.query_hash,
            normalized_query=entry.normalized_query,
            expires_at=entry.expires_at,
            source_retrieved_at=entry.source_retrieved_at,
        )

    async def put(
        self,
        *,
        namespace: str,
        query_hash: str,
        normalized_query: str,
        constraints: dict[str, object],
        embedding: tuple[float, ...],
        payload: dict[str, object],
        expires_at: datetime,
        source_retrieved_at: datetime | None,
    ) -> None:
        _validate_embedding(embedding)
        self._entries.append(
            _SemanticEntry(
                namespace=namespace,
                query_hash=query_hash,
                normalized_query=normalized_query,
                constraints=deepcopy(constraints),
                embedding=embedding,
                payload=deepcopy(payload),
                expires_at=_utc(expires_at),
                source_retrieved_at=(
                    _utc(source_retrieved_at)
                    if source_retrieved_at is not None
                    else None
                ),
            )
        )


class CacheCoordinator:
    """Try exact then semantic caching without making cache health critical."""

    def __init__(
        self,
        *,
        exact: ExactCacheStore | None = None,
        semantic: SemanticCacheStore | None = None,
        embedder: Embedder | None = None,
        semantic_threshold: float = 0.92,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 0.0 <= semantic_threshold <= 1.0:
            raise ValueError("Semantic threshold must be between 0 and 1.")
        if (semantic is None) != (embedder is None):
            raise ValueError("Semantic store and embedder must be configured together.")
        self._exact = exact
        self._semantic = semantic
        self._embedder = embedder
        self._threshold = semantic_threshold
        self._clock = clock or (lambda: datetime.now(UTC))

    async def acquire_fill_lock(
        self,
        fingerprint: QueryFingerprint,
        *,
        ttl_seconds: int,
    ) -> tuple[bool, str | None]:
        """Return ``(supported, token)`` for an optional exact-store lock."""
        acquire = getattr(self._exact, "acquire_fill_lock", None)
        if not callable(acquire):
            return False, None
        token = await acquire(fingerprint.exact_key, ttl_seconds=ttl_seconds)
        return True, token

    async def release_fill_lock(
        self,
        fingerprint: QueryFingerprint,
        token: str,
    ) -> None:
        """Release a supported exact-store lock without exposing its backend."""
        release = getattr(self._exact, "release_fill_lock", None)
        if callable(release):
            await release(fingerprint.exact_key, token)

    async def lookup(
        self,
        fingerprint: QueryFingerprint,
        *,
        now: datetime | None = None,
    ) -> CacheLookup:
        """Return the first valid hit, treating every cache error as a miss."""
        failures: list[CacheFailure] = []
        if self._exact is not None:
            with observe_span(
                "cache.exact",
                run_type="retriever",
                metadata={"operation": "lookup", "cache_tier": "exact"},
            ) as span:
                try:
                    payload = await self._exact.get(fingerprint.exact_key)
                    span.set_attribute("cache_status", "hit" if payload else "miss")
                    if payload is not None:
                        return CacheLookup(
                            tier="exact",
                            payload=payload,
                            failures=tuple(failures),
                        )
                except Exception as error:
                    span.mark_error(error)
                    failures.append(_failure("exact", "get", error))

        if not fingerprint.semantic_eligible or self._semantic is None:
            return CacheLookup(tier="miss", payload=None, failures=tuple(failures))

        assert self._embedder is not None
        with observe_span(
            "cache.embedding",
            run_type="embedding",
            metadata={"operation": "query", "cache_tier": "embedding"},
        ) as span:
            try:
                embedding = await self._embedder.embed_query(fingerprint.semantic_text)
                _validate_embedding(embedding)
                span.set_attributes(
                    {
                        "outcome": "ok",
                        "cache_status": "generated",
                        "dimensions": len(embedding),
                    }
                )
            except Exception as error:
                span.mark_error(error)
                failures.append(_failure("embedding", "embed", error))
                return CacheLookup(tier="miss", payload=None, failures=tuple(failures))

        with observe_span(
            "cache.semantic",
            run_type="retriever",
            metadata={
                "operation": "lookup",
                "cache_tier": "semantic",
                "threshold": self._threshold,
            },
        ) as span:
            try:
                hit = await self._semantic.lookup(
                    namespace=fingerprint.namespace,
                    constraints=fingerprint.constraints,
                    embedding=embedding,
                    threshold=self._threshold,
                    now=_utc(now or self._clock()),
                )
                span.set_attribute("cache_status", "hit" if hit else "miss")
                if hit is not None:
                    span.set_attribute("similarity", hit.similarity)
            except Exception as error:
                span.mark_error(error)
                failures.append(_failure("semantic", "lookup", error))
                return CacheLookup(tier="miss", payload=None, failures=tuple(failures))
        if hit is None:
            return CacheLookup(tier="miss", payload=None, failures=tuple(failures))
        if self._exact is not None:
            remaining = math.ceil(
                (hit.expires_at - _utc(now or self._clock())).total_seconds()
            )
            if remaining > 0:
                try:
                    await self._exact.set(fingerprint.exact_key, hit.payload, remaining)
                except Exception as error:
                    failures.append(_failure("exact", "set", error))
        return CacheLookup(
            tier="semantic",
            payload=hit.payload,
            similarity=hit.similarity,
            failures=tuple(failures),
        )

    async def store(
        self,
        fingerprint: QueryFingerprint,
        payload: dict[str, object],
        decision: CachePolicyDecision,
        *,
        now: datetime | None = None,
    ) -> CacheWriteResult:
        """Write eligible tiers independently so one backend cannot block another."""
        current = _utc(now or self._clock())
        if not decision.cacheable or decision.expires_at is None:
            return CacheWriteResult(exact_written=False, semantic_written=False)
        remaining = math.ceil((_utc(decision.expires_at) - current).total_seconds())
        if remaining <= 0:
            return CacheWriteResult(exact_written=False, semantic_written=False)

        failures: list[CacheFailure] = []
        exact_written = False
        semantic_written = False
        if self._exact is not None:
            with observe_span(
                "cache.exact",
                run_type="retriever",
                metadata={
                    "operation": "store",
                    "cache_tier": "exact",
                    "ttl_seconds": remaining,
                },
            ) as span:
                try:
                    await self._exact.set(fingerprint.exact_key, payload, remaining)
                    exact_written = True
                    span.set_attributes({"outcome": "ok", "cache_status": "stored"})
                except Exception as error:
                    span.mark_error(error)
                    failures.append(_failure("exact", "set", error))

        if fingerprint.semantic_eligible and self._semantic is not None:
            assert self._embedder is not None
            with observe_span(
                "cache.embedding",
                run_type="embedding",
                metadata={"operation": "document", "cache_tier": "embedding"},
            ) as span:
                try:
                    embedding = await self._embedder.embed_document(
                        fingerprint.semantic_text
                    )
                    _validate_embedding(embedding)
                    span.set_attributes(
                        {
                            "outcome": "ok",
                            "cache_status": "generated",
                            "dimensions": len(embedding),
                        }
                    )
                except Exception as error:
                    span.mark_error(error)
                    failures.append(_failure("embedding", "embed", error))
                    embedding = None
            if embedding is not None:
                with observe_span(
                    "cache.semantic",
                    run_type="retriever",
                    metadata={
                        "operation": "store",
                        "cache_tier": "semantic",
                        "ttl_seconds": remaining,
                    },
                ) as span:
                    try:
                        await self._semantic.put(
                            namespace=fingerprint.namespace,
                            query_hash=fingerprint.query_hash,
                            normalized_query=fingerprint.normalized_query,
                            constraints=fingerprint.constraints,
                            embedding=embedding,
                            payload=payload,
                            expires_at=_utc(decision.expires_at),
                            source_retrieved_at=decision.source_retrieved_at,
                        )
                        semantic_written = True
                        span.set_attributes({"outcome": "ok", "cache_status": "stored"})
                    except Exception as error:
                        span.mark_error(error)
                        failures.append(_failure("semantic", "put", error))

        return CacheWriteResult(
            exact_written=exact_written,
            semantic_written=semantic_written,
            failures=tuple(failures),
        )


def _failure(tier: str, operation: str, error: Exception) -> CacheFailure:
    return CacheFailure(  # type: ignore[arg-type]
        tier=tier,
        operation=operation,
        error_type=type(error).__name__,
    )


def _validate_embedding(embedding: tuple[float, ...]) -> None:
    if not embedding or any(not math.isfinite(value) for value in embedding):
        raise ValueError("Embedding must contain finite values.")


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    _validate_embedding(left)
    _validate_embedding(right)
    if len(left) != len(right):
        return -1.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return -1.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
