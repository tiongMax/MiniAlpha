"""Credential-free tests for the production cache adapters."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.cache.embeddings import GeminiEmbedder
from app.cache.redis_exact import RedisExactCache


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.closed = False

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ex, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def delete(self, key):
        return int(self.values.pop(key, None) is not None)

    async def eval(self, _script, _count, key, token):
        if self.values.get(key) != token:
            return 0
        del self.values[key]
        return 1

    async def ping(self):
        return True

    async def aclose(self):
        self.closed = True


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, dict[str, object]]] = []

    async def aembed_query(self, text, **kwargs):
        self.calls.append(("query", text, kwargs))
        return [3.0, 4.0, 0.0]

    async def aembed_documents(self, texts, **kwargs):
        self.calls.append(("documents", texts, kwargs))
        return [[0.0, 3.0, 4.0] for _ in texts]


def test_redis_exact_cache_round_trips_json_with_finite_ttl() -> None:
    async def exercise() -> None:
        client = FakeRedis()
        cache = RedisExactCache(client)
        await cache.set("hash", {"answer": "ok", "tokens": 12}, ttl_seconds=300)

        assert await cache.get("hash") == {"answer": "ok", "tokens": 12}
        key = "mini-alpha:research-cache:exact:hash"
        assert client.ttls[key] == 300
        assert json.loads(client.values[key]) == {"answer": "ok", "tokens": 12}
        await cache.delete("hash")
        assert await cache.get("hash") is None

    asyncio.run(exercise())


def test_redis_fill_lock_only_releases_current_owner() -> None:
    async def exercise() -> None:
        client = FakeRedis()
        cache = RedisExactCache(client)

        token = await cache.acquire_fill_lock("hash", ttl_seconds=15)
        assert token is not None
        assert await cache.acquire_fill_lock("hash", ttl_seconds=15) is None
        assert await cache.release_fill_lock("hash", "not-the-owner") is False
        assert await cache.release_fill_lock("hash", token) is True
        assert await cache.acquire_fill_lock("hash", ttl_seconds=15) is not None

    asyncio.run(exercise())


def test_redis_exact_cache_rejects_unbounded_entries() -> None:
    async def exercise() -> None:
        cache = RedisExactCache(FakeRedis())
        with pytest.raises(ValueError, match="TTL must be positive"):
            await cache.set("hash", {"answer": "ok"}, ttl_seconds=0)

    asyncio.run(exercise())


def test_gemini_embedder_uses_retrieval_task_types_and_normalizes() -> None:
    async def exercise() -> None:
        client = FakeEmbeddingClient()
        embedder = GeminiEmbedder(client=client, dimensions=3)

        query = await embedder.embed_query("AAPL valuation")
        document = await embedder.embed_document(
            "Apple valuation",
            title="AAPL valuation",
        )

        assert query == pytest.approx((0.6, 0.8, 0.0))
        assert document == pytest.approx((0.0, 0.6, 0.8))
        assert client.calls == [
            (
                "query",
                "AAPL valuation",
                {
                    "task_type": "RETRIEVAL_QUERY",
                    "output_dimensionality": 3,
                },
            ),
            (
                "documents",
                ["Apple valuation"],
                {
                    "task_type": "RETRIEVAL_DOCUMENT",
                    "titles": ["AAPL valuation"],
                    "output_dimensionality": 3,
                },
            ),
        ]

    asyncio.run(exercise())


class AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_exc):
        return False


class FakeCursor:
    def __init__(self, *, row=None, rowcount=0) -> None:
        self.row = row
        self.rowcount = rowcount
        self.executions: list[tuple[str, tuple[object, ...] | None]] = []

    async def execute(self, statement, parameters=None):
        self.executions.append((statement, parameters))

    async def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self, **_kwargs):
        return AsyncContext(self._cursor)


class FakePool:
    def __init__(self, cursor: FakeCursor) -> None:
        self._connection = FakeConnection(cursor)

    def connection(self):
        return AsyncContext(self._connection)


def test_postgres_semantic_cache_filters_namespace_constraints_and_expiry() -> None:
    from app.cache.postgres_semantic import PostgresSemanticCache

    async def exercise() -> None:
        now = datetime.now(UTC)
        cursor = FakeCursor(
            row={
                "query_hash": "a" * 64,
                "normalized_query": "aapl valuation",
                "payload": {"answer": "cached"},
                "source_retrieved_at": now - timedelta(minutes=1),
                "expires_at": now + timedelta(minutes=14),
                "similarity": 0.96,
            }
        )
        cache = PostgresSemanticCache(FakePool(cursor), dimensions=3)

        hit = await cache.lookup(
            namespace="graph:v1:model:gemini",
            constraints={"tickers": ["AAPL"], "intents": ["valuation"]},
            embedding=(1.0, 0.0, 0.0),
            threshold=0.94,
            now=now,
        )

        assert hit is not None
        assert hit.payload == {"answer": "cached"}
        assert hit.similarity == pytest.approx(0.96)
        statement, parameters = cursor.executions[0]
        assert "cache.namespace = %s" in statement
        assert "cache.constraints = %s" in statement
        assert "cache.expires_at > %s" in statement
        assert parameters is not None
        assert parameters[0] == "[1,0,0]"
        assert parameters[-1] == pytest.approx(0.06)

    asyncio.run(exercise())


def test_postgres_semantic_cache_upserts_and_rejects_bad_vectors() -> None:
    from app.cache.postgres_semantic import PostgresSemanticCache

    async def exercise() -> None:
        now = datetime.now(UTC)
        cursor = FakeCursor()
        cache = PostgresSemanticCache(FakePool(cursor), dimensions=3)

        await cache.put(
            namespace="graph:v1",
            query_hash="b" * 64,
            normalized_query="msft overview",
            constraints={"tickers": ["MSFT"]},
            embedding=(0.0, 1.0, 0.0),
            payload={"answer": "cached"},
            expires_at=now + timedelta(minutes=15),
            source_retrieved_at=now,
        )
        assert (
            "ON CONFLICT (namespace, query_hash) DO UPDATE" in cursor.executions[0][0]
        )

        with pytest.raises(ValueError, match="Expected 3"):
            await cache.lookup(
                namespace="graph:v1",
                constraints={},
                embedding=(1.0, 0.0),
                threshold=0.9,
                now=now,
            )

    asyncio.run(exercise())
