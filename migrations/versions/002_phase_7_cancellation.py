"""Add the cancelled run lifecycle state.

Revision ID: 002_phase_7_cancellation
Revises: 001_phase_4_5_persistence
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "002_phase_7_cancellation"
down_revision: str | Sequence[str] | None = "001_phase_4_5_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow cancelled as a terminal thread and run state."""
    op.execute(
        """
        ALTER TABLE conversation_threads
        DROP CONSTRAINT conversation_threads_current_status_check
        """
    )
    op.execute(
        """
        ALTER TABLE conversation_threads
        ADD CONSTRAINT conversation_threads_current_status_check
        CHECK (current_status IN ('in_progress', 'completed', 'error', 'cancelled'))
        """
    )
    op.execute(
        """
        ALTER TABLE conversation_responses
        DROP CONSTRAINT conversation_responses_status_check
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
        ADD CONSTRAINT conversation_responses_status_check
        CHECK (status IN ('in_progress', 'completed', 'error', 'cancelled'))
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


def downgrade() -> None:
    """Restore the Phase 4-6 lifecycle constraints."""
    op.execute(
        """
        UPDATE conversation_responses
        SET status = 'error', error_code = 'research_failed',
            error_message = 'The research run did not complete.'
        WHERE status = 'cancelled'
        """
    )
    op.execute(
        """
        UPDATE conversation_threads SET current_status = 'error'
        WHERE current_status = 'cancelled'
        """
    )
    op.execute(
        """ALTER TABLE conversation_responses
        DROP CONSTRAINT ck_conversation_responses_lifecycle"""
    )
    op.execute(
        """ALTER TABLE conversation_responses
        DROP CONSTRAINT conversation_responses_status_check"""
    )
    op.execute(
        """ALTER TABLE conversation_responses
        ADD CONSTRAINT conversation_responses_status_check
        CHECK (status IN ('in_progress', 'completed', 'error'))"""
    )
    op.execute(
        """ALTER TABLE conversation_responses
        ADD CONSTRAINT ck_conversation_responses_lifecycle
        CHECK (
            (status = 'in_progress' AND answer IS NULL AND error_code IS NULL
                AND error_message IS NULL AND completed_at IS NULL)
            OR (status = 'completed' AND answer IS NOT NULL AND error_code IS NULL
                AND error_message IS NULL AND completed_at IS NOT NULL)
            OR (status = 'error' AND answer IS NULL AND error_code IS NOT NULL
                AND error_message IS NOT NULL AND completed_at IS NOT NULL)
        )"""
    )
    op.execute(
        """ALTER TABLE conversation_threads
        DROP CONSTRAINT conversation_threads_current_status_check"""
    )
    op.execute(
        """ALTER TABLE conversation_threads
        ADD CONSTRAINT conversation_threads_current_status_check
        CHECK (current_status IN ('in_progress', 'completed', 'error'))"""
    )
