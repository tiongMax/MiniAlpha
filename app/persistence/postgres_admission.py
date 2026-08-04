"""Transactional PostgreSQL admission and idempotent replay."""

from typing import cast
from uuid import UUID

from psycopg import AsyncConnection, AsyncCursor
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.persistence.models import ConversationRun, ConversationThread, RunAdmission
from app.persistence.postgres_reader import PostgresConversationReader
from app.persistence.postgres_records import thread_from_row
from app.persistence.repository import (
    ConversationPersistenceError,
    RequestKeyConflictError,
    RunInProgressError,
    ThreadNotFoundError,
)


class PostgresRunAdmission:
    """Allocate durable runs and resolve client retransmissions."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[DictRow]],
        reader: PostgresConversationReader,
    ) -> None:
        self._pool = pool
        self._reader = reader

    async def admit_run(
        self,
        *,
        thread_id: UUID | None,
        message: str,
        request_key: UUID | None,
    ) -> RunAdmission:
        """Atomically allocate a turn or recover an idempotent request."""
        try:
            async with self._pool.connection() as connection:
                async with connection.transaction():
                    async with connection.cursor(row_factory=dict_row) as cursor:
                        if request_key is not None:
                            existing = await self._reader.fetch_run_by_request_key(
                                cursor,
                                request_key,
                            )
                            if existing is not None:
                                return await self._replay_admission(
                                    cursor,
                                    existing,
                                    thread_id=thread_id,
                                    message=message,
                                )

                        thread = await self._lock_or_create_thread(cursor, thread_id)
                        active = await self._reader.fetch_active_run(
                            cursor,
                            thread.thread_id,
                        )
                        if active is not None:
                            raise RunInProgressError(
                                "The research thread already has an active run."
                            )

                        run = await self._insert_run(
                            cursor,
                            thread=thread,
                            message=message,
                            request_key=request_key,
                        )
                        await cursor.execute(
                            """
                            UPDATE conversation_threads
                            SET current_status = 'in_progress',
                                next_turn_index = next_turn_index + 1,
                                updated_at = NOW()
                            WHERE conversation_thread_id = %s
                            """,
                            (thread.thread_id,),
                        )
                        return RunAdmission(
                            run=run,
                            from_checkpoint_id=thread.latest_checkpoint_id,
                            replayed=False,
                        )
        except UniqueViolation as error:
            constraint = error.diag.constraint_name
            if constraint == "uq_conversation_responses_active_thread":
                raise RunInProgressError(
                    "The research thread already has an active run."
                ) from error
            if (
                constraint == "uq_conversation_responses_request_key"
                and request_key is not None
            ):
                existing = await self._reader.get_run_by_request_key(request_key)
                if existing is not None:
                    return await self._resolve_raced_request_key(
                        existing,
                        thread_id=thread_id,
                        message=message,
                    )
            raise ConversationPersistenceError(
                "The conversation run could not be admitted."
            ) from error

    async def _lock_or_create_thread(
        self,
        cursor: AsyncCursor[DictRow],
        thread_id: UUID | None,
    ) -> ConversationThread:
        if thread_id is None:
            await cursor.execute(
                """
                INSERT INTO conversation_threads DEFAULT VALUES
                RETURNING conversation_thread_id,
                          current_status,
                          title,
                          latest_checkpoint_id,
                          next_turn_index,
                          created_at,
                          updated_at
                """
            )
        else:
            await cursor.execute(
                """
                SELECT conversation_thread_id,
                       current_status,
                       title,
                       latest_checkpoint_id,
                       next_turn_index,
                       created_at,
                       updated_at
                FROM conversation_threads
                WHERE conversation_thread_id = %s
                FOR UPDATE
                """,
                (thread_id,),
            )
        row = await cursor.fetchone()
        if row is None:
            raise ThreadNotFoundError("The research thread was not found.")
        return thread_from_row(row)

    async def _insert_run(
        self,
        cursor: AsyncCursor[DictRow],
        *,
        thread: ConversationThread,
        message: str,
        request_key: UUID | None,
    ) -> ConversationRun:
        await cursor.execute(
            """
            INSERT INTO conversation_queries (
                conversation_thread_id,
                turn_index,
                content
            )
            VALUES (%s, %s, %s)
            RETURNING conversation_query_id
            """,
            (thread.thread_id, thread.next_turn_index, message),
        )
        query_row = await cursor.fetchone()
        if query_row is None:
            raise ConversationPersistenceError(
                "The conversation query could not be created."
            )
        await cursor.execute(
            """
            INSERT INTO conversation_responses (
                conversation_query_id,
                conversation_thread_id,
                turn_index,
                attempt_no,
                request_key,
                status
            )
            VALUES (%s, %s, %s, 1, %s, 'in_progress')
            RETURNING conversation_response_id
            """,
            (
                query_row["conversation_query_id"],
                thread.thread_id,
                thread.next_turn_index,
                request_key,
            ),
        )
        response_row = await cursor.fetchone()
        if response_row is None:
            raise ConversationPersistenceError(
                "The conversation run could not be created."
            )
        run = await self._reader.fetch_run(
            cursor,
            cast(UUID, response_row["conversation_response_id"]),
        )
        if run is None:
            raise ConversationPersistenceError(
                "The conversation run could not be loaded."
            )
        return run

    async def _replay_admission(
        self,
        cursor: AsyncCursor[DictRow],
        run: ConversationRun,
        *,
        thread_id: UUID | None,
        message: str,
    ) -> RunAdmission:
        self._validate_replay(run, thread_id=thread_id, message=message)
        await cursor.execute(
            """
            SELECT latest_checkpoint_id
            FROM conversation_threads
            WHERE conversation_thread_id = %s
            """,
            (run.thread_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ThreadNotFoundError("The research thread was not found.")
        return RunAdmission(
            run=run,
            from_checkpoint_id=cast(str | None, row["latest_checkpoint_id"]),
            replayed=True,
        )

    async def _resolve_raced_request_key(
        self,
        run: ConversationRun,
        *,
        thread_id: UUID | None,
        message: str,
    ) -> RunAdmission:
        self._validate_replay(run, thread_id=thread_id, message=message)
        thread = await self._reader.get_thread(run.thread_id)
        if thread is None:
            raise ThreadNotFoundError("The research thread was not found.")
        return RunAdmission(
            run=run,
            from_checkpoint_id=thread.latest_checkpoint_id,
            replayed=True,
        )

    @staticmethod
    def _validate_replay(
        run: ConversationRun,
        *,
        thread_id: UUID | None,
        message: str,
    ) -> None:
        if run.message != message or (
            thread_id is not None and run.thread_id != thread_id
        ):
            raise RequestKeyConflictError(
                "The request key belongs to a different request."
            )
