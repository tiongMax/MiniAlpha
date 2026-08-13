"""Persist structured tool-failure metadata.

Revision ID: 005_structured_failures
Revises: 004_point_2_semantic_cache
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "005_structured_failures"
down_revision: str | Sequence[str] | None = "004_point_2_semantic_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add an optional versioned failure object beside legacy error text."""
    op.execute(
        """
        ALTER TABLE conversation_artifacts
        ADD COLUMN failure JSONB,
        ADD CONSTRAINT ck_conversation_artifacts_failure_object
            CHECK (failure IS NULL OR JSONB_TYPEOF(failure) = 'object'),
        ADD CONSTRAINT ck_conversation_artifacts_failure_status
            CHECK (failure IS NULL OR status = 'error')
        """
    )


def downgrade() -> None:
    """Remove structured metadata while retaining legacy error strings."""
    op.execute(
        """
        ALTER TABLE conversation_artifacts
        DROP CONSTRAINT ck_conversation_artifacts_failure_status,
        DROP CONSTRAINT ck_conversation_artifacts_failure_object,
        DROP COLUMN failure
        """
    )
