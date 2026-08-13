"""Lifecycle and production composition for stateless result caching."""

from __future__ import annotations

import os
from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tool_registry import ToolRegistry
from app.cache.embeddings import GeminiEmbedder
from app.cache.models import CacheNamespace
from app.cache.postgres_semantic import PostgresSemanticCache
from app.cache.redis_exact import RedisExactCache
from app.cache.service import ResearchResultCacheService
from app.cache.stores import CacheCoordinator
from app.config import get_boolean, get_float, get_positive_int


@dataclass(slots=True)
class CacheRuntime:
    """Owned Redis and PostgreSQL resources for result caching."""

    service: ResearchResultCacheService
    exact: RedisExactCache
    pool: AsyncConnectionPool | None = None

    @classmethod
    async def open(
        cls,
        *,
        redis_url: str,
        database_url: str,
        registry: ToolRegistry,
        generation_model: str,
        api_key: str,
    ) -> CacheRuntime:
        """Open exact caching and optional semantic caching."""
        exact = await RedisExactCache.open(redis_url)
        pool: AsyncConnectionPool | None = None
        try:
            embedding_model = os.getenv(
                "CACHE_EMBEDDING_MODEL", "models/gemini-embedding-001"
            ).strip()
            dimensions = get_positive_int("CACHE_EMBEDDING_DIMENSIONS", 768)
            semantic_enabled = get_boolean("SEMANTIC_CACHE_ENABLED", True)
            semantic = None
            embedder = None
            if semantic_enabled:
                pool = AsyncConnectionPool(
                    conninfo=database_url,
                    min_size=1,
                    max_size=3,
                    open=False,
                    kwargs={"connect_timeout": 5},
                )
                await pool.open(wait=True, timeout=10)
                semantic = PostgresSemanticCache(pool, dimensions=dimensions)
                if not await semantic.is_ready():
                    raise RuntimeError("Semantic cache storage is not ready.")
                embedder = GeminiEmbedder(
                    api_key=api_key,
                    model=embedding_model,
                    dimensions=dimensions,
                )

            namespace = CacheNamespace(
                model=generation_model,
                prompt_version=_hash_text(SYSTEM_PROMPT),
                graph_version=os.getenv("CACHE_GRAPH_VERSION", "1").strip(),
                tool_schema_version=os.getenv("CACHE_TOOL_SCHEMA_VERSION", "1").strip(),
                policy_version=os.getenv("CACHE_POLICY_VERSION", "1").strip(),
                embedding_model=embedding_model if semantic_enabled else "none",
                embedding_dimensions=dimensions if semantic_enabled else 0,
            )
            coordinator = CacheCoordinator(
                exact=exact,
                semantic=semantic,
                embedder=embedder,
                semantic_threshold=get_float("CACHE_SEMANTIC_THRESHOLD", 0.92),
            )
            return cls(
                service=ResearchResultCacheService(
                    coordinator,
                    namespace=namespace,
                    registry=registry,
                    max_payload_bytes=get_positive_int(
                        "CACHE_MAX_PAYLOAD_BYTES", 1_000_000
                    ),
                    fill_lock_seconds=get_positive_int("CACHE_FILL_LOCK_SECONDS", 30),
                    fill_wait_seconds=get_float("CACHE_FILL_WAIT_SECONDS", 30.0),
                ),
                exact=exact,
                pool=pool,
            )
        except Exception:
            if pool is not None:
                await pool.close()
            await exact.close()
            raise

    async def close(self) -> None:
        """Close every resource owned by this cache runtime."""
        if self.pool is not None:
            await self.pool.close()
        await self.exact.close()


def _hash_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
