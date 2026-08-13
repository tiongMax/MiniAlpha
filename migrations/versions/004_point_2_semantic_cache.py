"""Add pgvector-backed semantic research-result cache.

Revision ID: 004_point_2_semantic_cache
Revises: 003_phase_7_partial_cancel
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004_point_2_semantic_cache"
down_revision: str | Sequence[str] | None = "003_phase_7_partial_cancel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install pgvector and create the expiring semantic cache table."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE semantic_research_cache (
            semantic_cache_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            namespace TEXT NOT NULL CHECK (LENGTH(BTRIM(namespace)) > 0),
            query_hash CHAR(64) NOT NULL
                CHECK (query_hash ~ '^[0-9a-f]{64}$'),
            normalized_query TEXT NOT NULL
                CHECK (LENGTH(BTRIM(normalized_query)) > 0),
            constraints JSONB NOT NULL DEFAULT '{}'::jsonb
                CHECK (JSONB_TYPEOF(constraints) = 'object'),
            embedding VECTOR(768) NOT NULL,
            payload JSONB NOT NULL CHECK (JSONB_TYPEOF(payload) = 'object'),
            source_retrieved_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_semantic_research_cache_namespace_query
                UNIQUE (namespace, query_hash),
            CONSTRAINT ck_semantic_research_cache_expiry
                CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_semantic_research_cache_namespace_expiry
        ON semantic_research_cache (namespace, expires_at)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_semantic_research_cache_embedding_hnsw
        ON semantic_research_cache
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    """Remove semantic cache data while retaining a shared pgvector extension."""
    op.execute("DROP TABLE semantic_research_cache")
