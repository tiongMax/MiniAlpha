"""PostgreSQL/pgvector semantic research-result cache adapter."""

import math
from datetime import datetime
from typing import cast

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.cache.models import SemanticCacheHit


class PostgresSemanticCache:
    """Search and persist policy-partitioned semantic cache entries."""

    def __init__(self, pool: object, *, dimensions: int = 768) -> None:
        if dimensions <= 0:
            raise ValueError("Embedding dimensions must be positive.")
        self._pool = pool
        self.dimensions = dimensions

    async def lookup(
        self,
        *,
        namespace: str,
        constraints: dict[str, object],
        embedding: tuple[float, ...],
        threshold: float,
        now: datetime,
    ) -> SemanticCacheHit | None:
        """Return the closest unexpired entry satisfying strict constraints."""
        self._validate_namespace(namespace)
        self._validate_time(now, field="now")
        if not 0 <= threshold <= 1:
            raise ValueError("Semantic similarity threshold must be between 0 and 1.")
        vector = self._vector_literal(embedding)
        max_distance = 1.0 - threshold
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    """
                    WITH query_vector AS (
                        SELECT %s::vector AS embedding
                    )
                    SELECT cache.query_hash,
                           cache.normalized_query,
                           cache.payload,
                           cache.source_retrieved_at,
                           cache.expires_at,
                           1 - (cache.embedding <=> query_vector.embedding)
                               AS similarity
                    FROM semantic_research_cache AS cache
                    CROSS JOIN query_vector
                    WHERE cache.namespace = %s
                      AND cache.constraints = %s
                      AND cache.expires_at > %s
                      AND (cache.embedding <=> query_vector.embedding) <= %s
                    ORDER BY cache.embedding <=> query_vector.embedding,
                             cache.expires_at DESC
                    LIMIT 1
                    """,
                    (
                        vector,
                        namespace,
                        Jsonb(constraints),
                        now,
                        max_distance,
                    ),
                )
                row = await cursor.fetchone()
        if row is None:
            return None
        payload = row["payload"]
        if not isinstance(payload, dict):
            raise ValueError("The semantic-cache payload is not a JSON object.")
        return SemanticCacheHit(
            payload=cast(dict[str, object], payload),
            similarity=float(row["similarity"]),
            query_hash=str(row["query_hash"]).strip(),
            normalized_query=str(row["normalized_query"]),
            expires_at=cast(datetime, row["expires_at"]),
            source_retrieved_at=cast(datetime | None, row["source_retrieved_at"]),
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
        """Insert an entry, replacing an older value for the exact query hash."""
        self._validate_namespace(namespace)
        if len(query_hash) != 64 or any(
            character not in "0123456789abcdef" for character in query_hash
        ):
            raise ValueError("Query hash must be 64 lowercase hexadecimal characters.")
        if not normalized_query.strip():
            raise ValueError("Normalized query cannot be empty.")
        self._validate_time(expires_at, field="expires_at")
        if source_retrieved_at is not None:
            self._validate_time(source_retrieved_at, field="source_retrieved_at")
        vector = self._vector_literal(embedding)
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO semantic_research_cache (
                        namespace,
                        query_hash,
                        normalized_query,
                        constraints,
                        embedding,
                        payload,
                        source_retrieved_at,
                        expires_at
                    )
                    VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s)
                    ON CONFLICT (namespace, query_hash) DO UPDATE SET
                        normalized_query = EXCLUDED.normalized_query,
                        constraints = EXCLUDED.constraints,
                        embedding = EXCLUDED.embedding,
                        payload = EXCLUDED.payload,
                        source_retrieved_at = EXCLUDED.source_retrieved_at,
                        expires_at = EXCLUDED.expires_at,
                        updated_at = NOW()
                    """,
                    (
                        namespace,
                        query_hash,
                        normalized_query,
                        Jsonb(constraints),
                        vector,
                        Jsonb(payload),
                        source_retrieved_at,
                        expires_at,
                    ),
                )

    async def delete_expired(self, *, now: datetime) -> int:
        """Delete expired entries and return the affected row count."""
        self._validate_time(now, field="now")
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM semantic_research_cache WHERE expires_at <= %s",
                    (now,),
                )
                return max(int(cursor.rowcount), 0)

    async def is_ready(self) -> bool:
        """Return whether the cache table and vector extension are available."""
        try:
            async with self._pool.connection() as connection:
                async with connection.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        """
                        SELECT to_regclass('public.semantic_research_cache')
                                   IS NOT NULL AS has_table,
                               EXISTS (
                                   SELECT 1 FROM pg_extension WHERE extname = 'vector'
                               ) AS has_vector
                        """
                    )
                    row = await cursor.fetchone()
            return bool(row and row["has_table"] and row["has_vector"])
        except Exception:
            return False

    def _vector_literal(self, embedding: tuple[float, ...]) -> str:
        if len(embedding) != self.dimensions:
            raise ValueError(
                f"Expected {self.dimensions} embedding dimensions, "
                f"received {len(embedding)}."
            )
        values = [float(value) for value in embedding]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Embedding contains a non-finite value.")
        return "[" + ",".join(format(value, ".17g") for value in values) + "]"

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if not namespace.strip():
            raise ValueError("Semantic-cache namespace cannot be empty.")

    @staticmethod
    def _validate_time(value: datetime, *, field: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware.")


PgVectorSemanticCache = PostgresSemanticCache
