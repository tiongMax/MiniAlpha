"""Transactional PostgreSQL run finalization."""

from collections.abc import Sequence
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.persistence.models import ConversationRun, ConversationTurn
from app.persistence.postgres_reader import PostgresConversationReader
from app.persistence.postgres_records import artifact_values
from app.persistence.repository import (
    CheckpointConflictError,
    RunLifecycleConflictError,
    RunNotFoundError,
)


class PostgresRunLifecycle:
    """Complete or fail one previously admitted durable run."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[DictRow]],
        reader: PostgresConversationReader,
    ) -> None:
        self._pool = pool
        self._reader = reader

    async def complete_run(
        self,
        run_id: UUID,
        *,
        expected_checkpoint_id: str | None,
        checkpoint_id: str,
        answer: str,
        tool_calls: Sequence[dict[str, object]],
        artifacts: Sequence[dict[str, object]],
    ) -> ConversationTurn:
        """Finalize a run and publish its graph checkpoint with CAS."""
        stored_values = [
            artifact_values(ordinal, artifact)
            for ordinal, artifact in enumerate(artifacts)
        ]
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    run = self._require_in_progress(
                        await self._reader.lock_run(cursor, run_id)
                    )

                    await cursor.execute(
                        """
                        UPDATE conversation_threads
                        SET latest_checkpoint_id = %s,
                            current_status = 'completed',
                            updated_at = NOW()
                        WHERE conversation_thread_id = %s
                          AND latest_checkpoint_id IS NOT DISTINCT FROM %s
                        RETURNING conversation_thread_id
                        """,
                        (
                            checkpoint_id,
                            run.thread_id,
                            expected_checkpoint_id,
                        ),
                    )
                    if await cursor.fetchone() is None:
                        raise CheckpointConflictError(
                            "The research thread checkpoint changed during execution."
                        )

                    await cursor.execute(
                        """
                        UPDATE conversation_responses
                        SET status = 'completed',
                            answer = %s,
                            tool_calls = %s,
                            completed_at = NOW()
                        WHERE conversation_response_id = %s
                        """,
                        (answer, Jsonb(list(tool_calls)), run_id),
                    )
                    for values in stored_values:
                        await cursor.execute(
                            """
                            INSERT INTO conversation_artifacts (
                                conversation_response_id,
                                ordinal,
                                artifact_type,
                                schema_version,
                                status,
                                data,
                                error
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (run_id, *values),
                        )

                    completed = await self._reader.fetch_run(cursor, run_id)
                    if completed is None:
                        raise RunNotFoundError("The research run was not found.")
                    return ConversationTurn(
                        run=completed,
                        artifacts=await self._reader.fetch_artifacts(cursor, run_id),
                    )

    async def fail_run(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> ConversationRun:
        """Mark a run failed without publishing a graph checkpoint."""
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    run = self._require_in_progress(
                        await self._reader.lock_run(cursor, run_id)
                    )
                    await cursor.execute(
                        """
                        UPDATE conversation_responses
                        SET status = 'error',
                            error_code = %s,
                            error_message = %s,
                            completed_at = NOW()
                        WHERE conversation_response_id = %s
                        """,
                        (error_code, error_message, run_id),
                    )
                    await cursor.execute(
                        """
                        UPDATE conversation_threads
                        SET current_status = 'error',
                            updated_at = NOW()
                        WHERE conversation_thread_id = %s
                        """,
                        (run.thread_id,),
                    )
                    failed = await self._reader.fetch_run(cursor, run_id)
                    if failed is None:
                        raise RunNotFoundError("The research run was not found.")
                    return failed

    async def recover_abandoned_runs(self) -> int:
        """Atomically fail every run abandoned by an earlier process."""
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE conversation_responses
                        SET status = 'error',
                            error_code = 'process_interrupted',
                            error_message =
                                'The worker process stopped before completing the run.',
                            completed_at = NOW()
                        WHERE status = 'in_progress'
                        RETURNING conversation_thread_id
                        """
                    )
                    rows = await cursor.fetchall()
                    thread_ids = [row["conversation_thread_id"] for row in rows]
                    if thread_ids:
                        await cursor.execute(
                            """
                            UPDATE conversation_threads
                            SET current_status = 'error', updated_at = NOW()
                            WHERE conversation_thread_id = ANY(%s)
                            """,
                            (thread_ids,),
                        )
                    return len(rows)

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        partial_answer: str = "",
        tool_calls: Sequence[dict[str, object]] = (),
        artifacts: Sequence[dict[str, object]] = (),
    ) -> ConversationRun:
        """Mark a run cancelled without publishing a graph checkpoint."""
        stored_values = [
            artifact_values(ordinal, artifact)
            for ordinal, artifact in enumerate(artifacts)
        ]
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    run = self._require_in_progress(
                        await self._reader.lock_run(cursor, run_id)
                    )
                    await cursor.execute(
                        """
                        UPDATE conversation_responses
                        SET status = 'cancelled',
                            answer = %s,
                            tool_calls = %s,
                            error_code = 'cancelled',
                            error_message = 'The research run was cancelled.',
                            completed_at = NOW()
                        WHERE conversation_response_id = %s
                        """,
                        (partial_answer or None, Jsonb(list(tool_calls)), run_id),
                    )
                    for values in stored_values:
                        await cursor.execute(
                            """
                            INSERT INTO conversation_artifacts (
                                conversation_response_id,
                                ordinal,
                                artifact_type,
                                schema_version,
                                status,
                                data,
                                error
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (run_id, *values),
                        )
                    await cursor.execute(
                        """
                        UPDATE conversation_threads
                        SET current_status = 'cancelled',
                            updated_at = NOW()
                        WHERE conversation_thread_id = %s
                        """,
                        (run.thread_id,),
                    )
                    cancelled = await self._reader.fetch_run(cursor, run_id)
                    if cancelled is None:
                        raise RunNotFoundError("The research run was not found.")
                    return cancelled

    @staticmethod
    def _require_in_progress(
        run: ConversationRun | None,
    ) -> ConversationRun:
        if run is None:
            raise RunNotFoundError("The research run was not found.")
        if run.status != "in_progress":
            raise RunLifecycleConflictError(
                "The research run is already in a terminal state."
            )
        return run
