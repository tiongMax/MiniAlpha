"""Allow cancelled runs to retain partial streamed output.

Revision ID: 003_phase_7_partial_cancel
Revises: 002_phase_7_cancellation
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "003_phase_7_partial_cancel"
down_revision: str | Sequence[str] | None = "002_phase_7_cancellation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Permit an optional answer on otherwise valid cancelled runs."""
    op.execute(
        """
        ALTER TABLE conversation_responses
        DROP CONSTRAINT ck_conversation_responses_lifecycle
        """
    )
    op.execute(
        """
        ALTER TABLE conversation_responses
        ADD CONSTRAINT ck_conversation_responses_lifecycle
        CHECK (
            (status = 'in_progress' AND answer IS NULL AND error_code IS NULL
                AND error_message IS NULL AND completed_at IS NULL)
            OR (status = 'completed' AND answer IS NOT NULL AND error_code IS NULL
                AND error_message IS NULL AND completed_at IS NOT NULL)
            OR (status = 'error' AND answer IS NULL AND error_code IS NOT NULL
                AND error_message IS NOT NULL AND completed_at IS NOT NULL)
            OR (status = 'cancelled' AND error_code IS NOT NULL
                AND error_message IS NOT NULL AND completed_at IS NOT NULL)
        )
        """
    )


def downgrade() -> None:
    """Remove partial answers before restoring the Phase 7 cancellation rule."""
    op.execute(
        """
        UPDATE conversation_responses SET answer = NULL
        WHERE status = 'cancelled'
        """
    )
    op.execute(
        """
        ALTER TABLE conversation_responses
        DROP CONSTRAINT ck_conversation_responses_lifecycle
        """
    )
    op.execute(
        """
        ALTER TABLE conversation_responses
        ADD CONSTRAINT ck_conversation_responses_lifecycle
        CHECK (
            (status = 'in_progress' AND answer IS NULL AND error_code IS NULL
                AND error_message IS NULL AND completed_at IS NULL)
            OR (status = 'completed' AND answer IS NOT NULL AND error_code IS NULL
                AND error_message IS NULL AND completed_at IS NOT NULL)
            OR (status IN ('error', 'cancelled') AND answer IS NULL
                AND error_code IS NOT NULL AND error_message IS NOT NULL
                AND completed_at IS NOT NULL)
        )
        """
    )
