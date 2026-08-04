"""Psycopg implementation of durable conversation lifecycle persistence."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from psycopg import AsyncConnection, AsyncCursor
from psycopg.errors import UniqueViolation
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.persistence.artifacts import parse_artifact
from app.persistence.models import (
    ConversationRun,
    ConversationThread,
    ConversationTurn,
    RunAdmission,
    StoredArtifact,
    ThreadPage,
)
from app.persistence.repository import (
    CheckpointConflictError,
    ConversationPersistenceError,
    RequestKeyConflictError,
    RunInProgressError,
    RunLifecycleConflictError,
    RunNotFoundError,
    ThreadNotFoundError,
)

_RUN_COLUMNS = """
    r.conversation_response_id,
    r.conversation_query_id,
    r.conversation_thread_id,
    r.turn_index,
    r.attempt_no,
    r.request_key,
    r.status,
    q.content,
    r.answer,
    r.tool_calls,
    r.error_code,
    r.error_message,
    r.started_at,
    r.completed_at
"""


class PostgresConversationRepository:
    """Persist thread and run lifecycle state with explicit PostgreSQL SQL."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[DictRow]],
    ) -> None:
        self._pool = pool

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
                            existing = await self._fetch_run_by_request_key(
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
                        active = await self._fetch_active_run(
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
                existing = await self.get_run_by_request_key(request_key)
                if existing is not None:
                    return await self._resolve_raced_request_key(
                        existing,
                        thread_id=thread_id,
                        message=message,
                    )
            raise ConversationPersistenceError(
                "The conversation run could not be admitted."
            ) from error

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
        artifact_values = [
            self._artifact_values(ordinal, artifact)
            for ordinal, artifact in enumerate(artifacts)
        ]
        async with self._pool.connection() as connection:
            async with connection.transaction():
                async with connection.cursor(row_factory=dict_row) as cursor:
                    run = await self._lock_run(cursor, run_id)
                    self._require_in_progress(run)

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
                    for values in artifact_values:
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

                    completed = await self._fetch_run(cursor, run_id)
                    if completed is None:
                        raise RunNotFoundError("The research run was not found.")
                    stored_artifacts = await self._fetch_artifacts(cursor, run_id)
                    return ConversationTurn(
                        run=completed,
                        artifacts=stored_artifacts,
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
                    run = await self._lock_run(cursor, run_id)
                    self._require_in_progress(run)
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
                    failed = await self._fetch_run(cursor, run_id)
                    if failed is None:
                        raise RunNotFoundError("The research run was not found.")
                    return failed

    async def get_thread(self, thread_id: UUID) -> ConversationThread | None:
        """Return one PostgreSQL-backed thread."""
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
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
                    """,
                    (thread_id,),
                )
                row = await cursor.fetchone()
                return self._thread_from_row(row) if row is not None else None

    async def list_threads(self, *, limit: int, offset: int) -> ThreadPage:
        """Return a page of threads ordered by recent activity."""
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) AS total FROM conversation_threads"
                )
                count_row = await cursor.fetchone()
                total = int(count_row["total"]) if count_row is not None else 0
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
                    ORDER BY updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = await cursor.fetchall()
                return ThreadPage(
                    threads=tuple(self._thread_from_row(row) for row in rows),
                    total=total,
                    limit=limit,
                    offset=offset,
                )

    async def get_run_by_request_key(
        self,
        request_key: UUID,
    ) -> ConversationRun | None:
        """Resolve a globally unique request key."""
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                return await self._fetch_run_by_request_key(cursor, request_key)

    async def get_turn(self, run_id: UUID) -> ConversationTurn | None:
        """Return one PostgreSQL run and its ordered artifacts."""
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                run = await self._fetch_run(cursor, run_id)
                if run is None:
                    return None
                return ConversationTurn(
                    run=run,
                    artifacts=await self._fetch_artifacts(cursor, run_id),
                )

    async def list_turns(self, thread_id: UUID) -> tuple[ConversationTurn, ...]:
        """Return all persisted attempts for one thread."""
        if await self.get_thread(thread_id) is None:
            raise ThreadNotFoundError("The research thread was not found.")

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    f"""
                    SELECT {_RUN_COLUMNS}
                    FROM conversation_responses r
                    JOIN conversation_queries q
                      ON q.conversation_query_id = r.conversation_query_id
                    WHERE r.conversation_thread_id = %s
                    ORDER BY r.turn_index, r.attempt_no
                    """,
                    (thread_id,),
                )
                runs = tuple(self._run_from_row(row) for row in await cursor.fetchall())
                if not runs:
                    return ()
                await cursor.execute(
                    """
                    SELECT artifact_id,
                           conversation_response_id,
                           ordinal,
                           artifact_type,
                           schema_version,
                           status,
                           data,
                           error,
                           created_at
                    FROM conversation_artifacts
                    WHERE conversation_response_id = ANY(%s)
                    ORDER BY conversation_response_id, ordinal
                    """,
                    ([run.run_id for run in runs],),
                )
                grouped: dict[UUID, list[StoredArtifact]] = {}
                for row in await cursor.fetchall():
                    artifact = self._artifact_from_row(row)
                    grouped.setdefault(artifact.run_id, []).append(artifact)
                return tuple(
                    ConversationTurn(
                        run=run,
                        artifacts=tuple(grouped.get(run.run_id, [])),
                    )
                    for run in runs
                )

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
        return self._thread_from_row(row)

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
        run = await self._fetch_run(
            cursor,
            cast(UUID, response_row["conversation_response_id"]),
        )
        if run is None:
            raise ConversationPersistenceError(
                "The conversation run could not be loaded."
            )
        return run

    async def _lock_run(
        self,
        cursor: AsyncCursor[DictRow],
        run_id: UUID,
    ) -> ConversationRun:
        await cursor.execute(
            f"""
            SELECT {_RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.conversation_response_id = %s
            FOR UPDATE OF r
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RunNotFoundError("The research run was not found.")
        return self._run_from_row(row)

    async def _fetch_run(
        self,
        cursor: AsyncCursor[DictRow],
        run_id: UUID,
    ) -> ConversationRun | None:
        await cursor.execute(
            f"""
            SELECT {_RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.conversation_response_id = %s
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return self._run_from_row(row) if row is not None else None

    async def _fetch_run_by_request_key(
        self,
        cursor: AsyncCursor[DictRow],
        request_key: UUID,
    ) -> ConversationRun | None:
        await cursor.execute(
            f"""
            SELECT {_RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.request_key = %s
            """,
            (request_key,),
        )
        row = await cursor.fetchone()
        return self._run_from_row(row) if row is not None else None

    async def _fetch_active_run(
        self,
        cursor: AsyncCursor[DictRow],
        thread_id: UUID,
    ) -> ConversationRun | None:
        await cursor.execute(
            f"""
            SELECT {_RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.conversation_thread_id = %s
              AND r.status = 'in_progress'
            """,
            (thread_id,),
        )
        row = await cursor.fetchone()
        return self._run_from_row(row) if row is not None else None

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
        thread = await self.get_thread(run.thread_id)
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

    @staticmethod
    def _require_in_progress(run: ConversationRun) -> None:
        if run.status != "in_progress":
            raise RunLifecycleConflictError(
                "The research run is already in a terminal state."
            )

    async def _fetch_artifacts(
        self,
        cursor: AsyncCursor[DictRow],
        run_id: UUID,
    ) -> tuple[StoredArtifact, ...]:
        await cursor.execute(
            """
            SELECT artifact_id,
                   conversation_response_id,
                   ordinal,
                   artifact_type,
                   schema_version,
                   status,
                   data,
                   error,
                   created_at
            FROM conversation_artifacts
            WHERE conversation_response_id = %s
            ORDER BY ordinal
            """,
            (run_id,),
        )
        return tuple(self._artifact_from_row(row) for row in await cursor.fetchall())

    @staticmethod
    def _artifact_values(
        ordinal: int,
        artifact: Mapping[str, object],
    ) -> tuple[int, str, int, str, Jsonb | None, str | None]:
        parsed = parse_artifact(artifact)
        return (
            ordinal,
            parsed.artifact_type,
            parsed.schema_version,
            parsed.status,
            Jsonb(parsed.data) if parsed.data is not None else None,
            parsed.error,
        )

    @staticmethod
    def _thread_from_row(row: Mapping[str, object]) -> ConversationThread:
        return ConversationThread(
            thread_id=cast(UUID, row["conversation_thread_id"]),
            status=cast(
                Literal["in_progress", "completed", "error"],
                row["current_status"],
            ),
            title=cast(str | None, row["title"]),
            latest_checkpoint_id=cast(str | None, row["latest_checkpoint_id"]),
            next_turn_index=int(cast(int, row["next_turn_index"])),
            created_at=cast(datetime, row["created_at"]),
            updated_at=cast(datetime, row["updated_at"]),
        )

    @staticmethod
    def _run_from_row(row: Mapping[str, object]) -> ConversationRun:
        raw_calls = row["tool_calls"]
        calls = raw_calls if isinstance(raw_calls, list) else []
        return ConversationRun(
            run_id=cast(UUID, row["conversation_response_id"]),
            query_id=cast(UUID, row["conversation_query_id"]),
            thread_id=cast(UUID, row["conversation_thread_id"]),
            turn_index=int(cast(int, row["turn_index"])),
            attempt_no=int(cast(int, row["attempt_no"])),
            request_key=cast(UUID | None, row["request_key"]),
            status=cast(
                Literal["in_progress", "completed", "error"],
                row["status"],
            ),
            message=str(row["content"]),
            answer=cast(str | None, row["answer"]),
            tool_calls=tuple(
                cast(dict[str, object], call)
                for call in calls
                if isinstance(call, dict)
            ),
            error_code=cast(str | None, row["error_code"]),
            error_message=cast(str | None, row["error_message"]),
            started_at=cast(datetime, row["started_at"]),
            completed_at=cast(datetime | None, row["completed_at"]),
        )

    @staticmethod
    def _artifact_from_row(row: Mapping[str, object]) -> StoredArtifact:
        data = row["data"]
        return StoredArtifact(
            artifact_id=cast(UUID, row["artifact_id"]),
            run_id=cast(UUID, row["conversation_response_id"]),
            ordinal=int(cast(int, row["ordinal"])),
            artifact_type=str(row["artifact_type"]),
            schema_version=int(cast(int, row["schema_version"])),
            status=cast(Literal["ok", "error"], row["status"]),
            data=cast(dict[str, object], data) if isinstance(data, dict) else None,
            error=cast(str | None, row["error"]),
            created_at=cast(datetime, row["created_at"]),
        )
