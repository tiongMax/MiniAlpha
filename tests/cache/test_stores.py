"""Exact and semantic caching remains deterministic and failure-open."""

import asyncio
from datetime import UTC, datetime, timedelta

from app.cache.models import CachePolicyDecision
from app.cache.normalization import fingerprint_query
from app.cache.stores import (
    CacheCoordinator,
    InMemoryExactCache,
    InMemorySemanticCache,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
PAYLOAD: dict[str, object] = {"answer": "AAPL volatility is 24%."}


class StaticEmbedder:
    def __init__(self) -> None:
        self.query_calls = 0
        self.document_calls = 0

    async def embed_query(self, _text: str) -> tuple[float, ...]:
        self.query_calls += 1
        return (1.0, 0.0, 0.0)

    async def embed_document(self, _text: str) -> tuple[float, ...]:
        self.document_calls += 1
        return (1.0, 0.0, 0.0)


class RaisingExact:
    async def get(self, _key: str) -> dict[str, object] | None:
        raise ConnectionError("redis unavailable")

    async def set(
        self,
        _key: str,
        _payload: dict[str, object],
        _ttl_seconds: int,
    ) -> None:
        raise ConnectionError("redis unavailable")


class RaisingEmbedder:
    async def embed_query(self, _text: str) -> tuple[float, ...]:
        raise TimeoutError("embedding unavailable")

    async def embed_document(self, _text: str) -> tuple[float, ...]:
        raise TimeoutError("embedding unavailable")


def decision(*, ttl_seconds: int = 600) -> CachePolicyDecision:
    return CachePolicyDecision(
        cacheable=True,
        ttl_seconds=ttl_seconds,
        expires_at=NOW + timedelta(seconds=ttl_seconds),
        source_retrieved_at=NOW,
        artifact_types=("volatility",),
        reason="test",
    )


def test_exact_store_returns_defensive_copy_and_expires() -> None:
    clock = [NOW]
    store = InMemoryExactCache(clock=lambda: clock[0])

    async def scenario() -> None:
        await store.set("key", PAYLOAD, 10)
        hit = await store.get("key")
        assert hit == PAYLOAD
        assert hit is not PAYLOAD
        hit["answer"] = "mutated"
        assert await store.get("key") == PAYLOAD
        clock[0] += timedelta(seconds=10)
        assert await store.get("key") is None

    asyncio.run(scenario())


def test_coordinator_prefers_exact_hit_without_embedding() -> None:
    exact = InMemoryExactCache(clock=lambda: NOW)
    semantic = InMemorySemanticCache()
    embedder = StaticEmbedder()
    coordinator = CacheCoordinator(
        exact=exact,
        semantic=semantic,
        embedder=embedder,
        clock=lambda: NOW,
    )
    fingerprint = fingerprint_query("Show AAPL volatility over 1y", "test")

    async def scenario() -> None:
        stored = await coordinator.store(fingerprint, PAYLOAD, decision(), now=NOW)
        assert stored.exact_written and stored.semantic_written
        lookup = await coordinator.lookup(fingerprint, now=NOW)
        assert lookup.tier == "exact"
        assert lookup.payload == PAYLOAD
        assert embedder.query_calls == 0

    asyncio.run(scenario())


def test_semantic_hit_requires_identical_structural_constraints() -> None:
    exact = InMemoryExactCache(clock=lambda: NOW)
    semantic = InMemorySemanticCache()
    embedder = StaticEmbedder()
    coordinator = CacheCoordinator(
        exact=exact,
        semantic=semantic,
        embedder=embedder,
        clock=lambda: NOW,
    )
    original = fingerprint_query("Show AAPL volatility over 1y", "test")
    paraphrase = fingerprint_query(
        "Calculate the volatility of AAPL over 1y",
        "test",
    )
    different_symbol = fingerprint_query("Show MSFT volatility over 1y", "test")
    different_period = fingerprint_query("Show AAPL volatility over 5y", "test")

    async def scenario() -> None:
        await coordinator.store(original, PAYLOAD, decision(), now=NOW)
        hit = await coordinator.lookup(paraphrase, now=NOW)
        assert hit.tier == "semantic"
        assert hit.payload == PAYLOAD
        assert hit.similarity == 1.0
        assert (await coordinator.lookup(different_symbol, now=NOW)).tier == "miss"
        assert (await coordinator.lookup(different_period, now=NOW)).tier == "miss"

    asyncio.run(scenario())


def test_ineligible_query_never_invokes_embedding_or_semantic_store() -> None:
    embedder = StaticEmbedder()
    coordinator = CacheCoordinator(
        semantic=InMemorySemanticCache(),
        embedder=embedder,
        clock=lambda: NOW,
    )
    fingerprint = fingerprint_query("Show the latest AAPL news", "test")

    async def scenario() -> None:
        write = await coordinator.store(fingerprint, PAYLOAD, decision(), now=NOW)
        lookup = await coordinator.lookup(fingerprint, now=NOW)
        assert not write.semantic_written
        assert lookup.tier == "miss"
        assert embedder.query_calls == 0
        assert embedder.document_calls == 0

    asyncio.run(scenario())


def test_exact_backend_failure_is_a_diagnostic_miss_not_an_exception() -> None:
    coordinator = CacheCoordinator(exact=RaisingExact(), clock=lambda: NOW)
    fingerprint = fingerprint_query("Explain volatility", "test", intents=())

    lookup = asyncio.run(coordinator.lookup(fingerprint, now=NOW))
    write = asyncio.run(coordinator.store(fingerprint, PAYLOAD, decision(), now=NOW))

    assert lookup.tier == "miss"
    assert lookup.failures[0].tier == "exact"
    assert lookup.failures[0].error_type == "ConnectionError"
    assert not write.exact_written
    assert write.failures[0].operation == "set"


def test_exact_write_succeeds_when_embedding_backend_fails() -> None:
    exact = InMemoryExactCache(clock=lambda: NOW)
    coordinator = CacheCoordinator(
        exact=exact,
        semantic=InMemorySemanticCache(),
        embedder=RaisingEmbedder(),
        clock=lambda: NOW,
    )
    fingerprint = fingerprint_query("Show AAPL volatility over 1y", "test")

    async def scenario() -> None:
        write = await coordinator.store(fingerprint, PAYLOAD, decision(), now=NOW)
        assert write.exact_written
        assert not write.semantic_written
        assert write.failures[0].tier == "embedding"
        assert (await coordinator.lookup(fingerprint, now=NOW)).tier == "exact"

    asyncio.run(scenario())


def test_expired_policy_is_not_written() -> None:
    coordinator = CacheCoordinator(
        exact=InMemoryExactCache(clock=lambda: NOW),
        clock=lambda: NOW,
    )
    fingerprint = fingerprint_query("Explain volatility", "test", intents=())
    expired = CachePolicyDecision(
        cacheable=True,
        ttl_seconds=1,
        expires_at=NOW - timedelta(seconds=1),
        source_retrieved_at=None,
        artifact_types=(),
        reason="expired",
    )

    result = asyncio.run(coordinator.store(fingerprint, PAYLOAD, expired, now=NOW))

    assert not result.exact_written
    assert not result.semantic_written
