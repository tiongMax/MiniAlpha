"""Add durable conversation and run records.

Revision ID: 001_phase_4_5_persistence
Revises:
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001_phase_4_5_persistence"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create application-owned conversation persistence tables."""
    op.execute(
        """
        CREATE TABLE conversation_threads (
            conversation_thread_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            current_status TEXT NOT NULL DEFAULT 'in_progress'
                CHECK (current_status IN ('in_progress', 'completed', 'error')),
            title VARCHAR(255),
            latest_checkpoint_id TEXT,
            next_turn_index INTEGER NOT NULL DEFAULT 1
                CHECK (next_turn_index >= 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_conversation_threads_updated_at
        ON conversation_threads (updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX idx_conversation_threads_status
        ON conversation_threads (current_status)
        """
    )

    op.execute(
        """
        CREATE TABLE conversation_queries (
            conversation_query_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_thread_id UUID NOT NULL
                REFERENCES conversation_threads(conversation_thread_id)
                ON DELETE CASCADE,
            turn_index INTEGER NOT NULL CHECK (turn_index >= 1),
            content TEXT NOT NULL CHECK (LENGTH(BTRIM(content)) > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_conversation_queries_thread_turn
                UNIQUE (conversation_thread_id, turn_index),
            CONSTRAINT uq_conversation_queries_identity
                UNIQUE (
                    conversation_query_id,
                    conversation_thread_id,
                    turn_index
                )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_conversation_queries_thread
        ON conversation_queries (conversation_thread_id, turn_index)
        """
    )

    op.execute(
        """
        CREATE TABLE conversation_responses (
            conversation_response_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_query_id UUID NOT NULL,
            conversation_thread_id UUID NOT NULL
                REFERENCES conversation_threads(conversation_thread_id)
                ON DELETE CASCADE,
            turn_index INTEGER NOT NULL CHECK (turn_index >= 1),
            attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
            request_key UUID,
            status TEXT NOT NULL
                CHECK (status IN ('in_progress', 'completed', 'error')),
            answer TEXT,
            tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb
                CHECK (JSONB_TYPEOF(tool_calls) = 'array'),
            error_code TEXT,
            error_message TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            CONSTRAINT fk_conversation_responses_query
                FOREIGN KEY (
                    conversation_query_id,
                    conversation_thread_id,
                    turn_index
                )
                REFERENCES conversation_queries (
                    conversation_query_id,
                    conversation_thread_id,
                    turn_index
                )
                ON DELETE CASCADE,
            CONSTRAINT uq_conversation_responses_thread_turn_attempt
                UNIQUE (conversation_thread_id, turn_index, attempt_no),
            CONSTRAINT ck_conversation_responses_lifecycle
                CHECK (
                    (
                        status = 'in_progress'
                        AND answer IS NULL
                        AND error_code IS NULL
                        AND error_message IS NULL
                        AND completed_at IS NULL
                    )
                    OR
                    (
                        status = 'completed'
                        AND answer IS NOT NULL
                        AND error_code IS NULL
                        AND error_message IS NULL
                        AND completed_at IS NOT NULL
                    )
                    OR
                    (
                        status = 'error'
                        AND answer IS NULL
                        AND error_code IS NOT NULL
                        AND error_message IS NOT NULL
                        AND completed_at IS NOT NULL
                    )
                )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_conversation_responses_request_key
        ON conversation_responses (request_key)
        WHERE request_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_conversation_responses_active_thread
        ON conversation_responses (conversation_thread_id)
        WHERE status = 'in_progress'
        """
    )
    op.execute(
        """
        CREATE INDEX idx_conversation_responses_thread
        ON conversation_responses (
            conversation_thread_id,
            turn_index,
            attempt_no
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_conversation_responses_status
        ON conversation_responses (status)
        """
    )

    op.execute(
        """
        CREATE TABLE conversation_artifacts (
            artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_response_id UUID NOT NULL
                REFERENCES conversation_responses(conversation_response_id)
                ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            artifact_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
            status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
            data JSONB,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_conversation_artifacts_run_ordinal
                UNIQUE (conversation_response_id, ordinal),
            CONSTRAINT ck_conversation_artifacts_payload
                CHECK (
                    (status = 'ok' AND data IS NOT NULL AND error IS NULL)
                    OR
                    (status = 'error' AND data IS NULL AND error IS NOT NULL)
                )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_conversation_artifacts_run
        ON conversation_artifacts (conversation_response_id, ordinal)
        """
    )


def downgrade() -> None:
    """Remove application-owned conversation persistence tables."""
    op.execute("DROP TABLE conversation_artifacts")
    op.execute("DROP TABLE conversation_responses")
    op.execute("DROP TABLE conversation_queries")
    op.execute("DROP TABLE conversation_threads")
