"""Research-result cache bridge used by the stateless agent service."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from langchain_core.messages import HumanMessage

from app.agent.intent_router import IntentRouter
from app.agent.tool_registry import ToolRegistry
from app.cache.models import CacheNamespace
from app.cache.normalization import fingerprint_query
from app.cache.policy import evaluate_artifact_ttl
from app.cache.research_results import (
    deserialize_research_result,
    serialize_research_result,
)
from app.cache.stores import CacheCoordinator
from app.services.research_agent import (
    CachedResearchResult,
    CacheFillReservation,
    ResearchResult,
)


class ResearchResultCacheService:
    """Fingerprint, validate, and coordinate complete result caching."""

    def __init__(
        self,
        coordinator: CacheCoordinator,
        *,
        namespace: CacheNamespace,
        registry: ToolRegistry,
        max_payload_bytes: int = 1_000_000,
        fill_lock_seconds: int = 30,
        fill_wait_seconds: float = 30.0,
    ) -> None:
        if max_payload_bytes <= 0:
            raise ValueError("Cache payload limit must be positive.")
        if fill_lock_seconds <= 0 or fill_wait_seconds <= 0:
            raise ValueError("Cache fill lock and wait must be positive.")
        self._coordinator = coordinator
        self._namespace = namespace
        self._router = IntentRouter(registry)
        self._max_payload_bytes = max_payload_bytes
        self._fill_lock_seconds = fill_lock_seconds
        self._fill_wait_seconds = fill_wait_seconds

    async def lookup(self, message: str) -> CachedResearchResult | None:
        """Return a validated cache hit or fail open as a miss."""
        fingerprint = self._fingerprint(message)
        lookup = await self._coordinator.lookup(fingerprint)
        if lookup.payload is None or lookup.tier == "miss":
            return None
        try:
            result = deserialize_research_result(lookup.payload)
        except (TypeError, ValueError):
            return None
        status = "exact_hit" if lookup.tier == "exact" else "semantic_hit"
        return CachedResearchResult(result=result, status=status)

    async def store(self, message: str, result: ResearchResult) -> None:
        """Store only complete successful results under data-specific TTLs."""
        if result.checkpoint_id is not None or not result.answer.strip():
            return
        if any(call.status != "ok" for call in result.tool_calls):
            return
        decision = evaluate_artifact_ttl(result.artifacts)
        if not decision.cacheable:
            return
        fingerprint = self._fingerprint(message)
        if "relative_time_language" in fingerprint.semantic_ineligibility_reasons:
            ttl = min(decision.ttl_seconds, 60)
            decision = replace(
                decision,
                ttl_seconds=ttl,
                expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
                reason="relative_time_exact_ttl",
            )
        payload = serialize_research_result(result)
        encoded_size = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        if encoded_size > self._max_payload_bytes:
            return
        await self._coordinator.store(
            fingerprint,
            payload,
            decision,
        )

    async def acquire_fill(self, message: str) -> CacheFillReservation:
        """Acquire a Redis-backed single-flight lock when configured."""
        supported, token = await self._coordinator.acquire_fill_lock(
            self._fingerprint(message),
            ttl_seconds=self._fill_lock_seconds,
        )
        if not supported:
            return CacheFillReservation(owner=True)
        return CacheFillReservation(owner=token is not None, token=token)

    async def wait_for_fill(self, message: str) -> CachedResearchResult | None:
        """Poll for the owner's fill until a bounded deadline."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._fill_wait_seconds
        while loop.time() < deadline:
            await asyncio.sleep(0.05)
            cached = await self.lookup(message)
            if cached is not None:
                return cached
        return None

    async def release_fill(
        self,
        message: str,
        reservation: CacheFillReservation,
    ) -> None:
        """Release only a token owned by this request."""
        if reservation.owner and reservation.token is not None:
            await self._coordinator.release_fill_lock(
                self._fingerprint(message), reservation.token
            )

    def _fingerprint(self, message: str):
        route = self._router.route({"messages": [HumanMessage(content=message)]})
        return fingerprint_query(
            message,
            self._namespace,
            intents=route.intents,
        )
