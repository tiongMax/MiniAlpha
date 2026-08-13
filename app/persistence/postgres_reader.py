"""Read operations and shared row lookups for PostgreSQL conversations."""

from uuid import UUID

from psycopg import AsyncConnection, AsyncCursor
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.persistence.models import (
    ConversationRun,
    ConversationThread,
    ConversationTurn,
    StoredArtifact,
    ThreadPage,
)
from app.persistence.postgres_records import (
    RUN_COLUMNS,
    artifact_from_row,
    run_from_row,
    thread_from_row,
)
from app.persistence.repository import ThreadNotFoundError


class PostgresConversationReader:
    """Load durable conversation records through one shared async pool."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[DictRow]],
    ) -> None:
        self._pool = pool

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
                return thread_from_row(row) if row is not None else None

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
                    threads=tuple(thread_from_row(row) for row in rows),
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
                return await self.fetch_run_by_request_key(cursor, request_key)

    async def get_turn(self, run_id: UUID) -> ConversationTurn | None:
        """Return one PostgreSQL run and its ordered artifacts."""
        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                run = await self.fetch_run(cursor, run_id)
                if run is None:
                    return None
                return ConversationTurn(
                    run=run,
                    artifacts=await self.fetch_artifacts(cursor, run_id),
                )

    async def list_turns(self, thread_id: UUID) -> tuple[ConversationTurn, ...]:
        """Return all persisted attempts for one thread."""
        if await self.get_thread(thread_id) is None:
            raise ThreadNotFoundError("The research thread was not found.")

        async with self._pool.connection() as connection:
            async with connection.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(
                    f"""
                    SELECT {RUN_COLUMNS}
                    FROM conversation_responses r
                    JOIN conversation_queries q
                      ON q.conversation_query_id = r.conversation_query_id
                    WHERE r.conversation_thread_id = %s
                    ORDER BY r.turn_index, r.attempt_no
                    """,
                    (thread_id,),
                )
                runs = tuple(run_from_row(row) for row in await cursor.fetchall())
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
                           failure,
                           created_at
                    FROM conversation_artifacts
                    WHERE conversation_response_id = ANY(%s)
                    ORDER BY conversation_response_id, ordinal
                    """,
                    ([run.run_id for run in runs],),
                )
                grouped: dict[UUID, list[StoredArtifact]] = {}
                for row in await cursor.fetchall():
                    artifact = artifact_from_row(row)
                    grouped.setdefault(artifact.run_id, []).append(artifact)
                return tuple(
                    ConversationTurn(
                        run=run,
                        artifacts=tuple(grouped.get(run.run_id, [])),
                    )
                    for run in runs
                )

    async def lock_run(
        self,
        cursor: AsyncCursor[DictRow],
        run_id: UUID,
    ) -> ConversationRun | None:
        """Lock and return a run inside the caller's transaction."""
        await cursor.execute(
            f"""
            SELECT {RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.conversation_response_id = %s
            FOR UPDATE OF r
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return run_from_row(row) if row is not None else None

    async def fetch_run(
        self,
        cursor: AsyncCursor[DictRow],
        run_id: UUID,
    ) -> ConversationRun | None:
        """Return a run through an existing cursor."""
        await cursor.execute(
            f"""
            SELECT {RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.conversation_response_id = %s
            """,
            (run_id,),
        )
        row = await cursor.fetchone()
        return run_from_row(row) if row is not None else None

    async def fetch_run_by_request_key(
        self,
        cursor: AsyncCursor[DictRow],
        request_key: UUID,
    ) -> ConversationRun | None:
        """Return a run by idempotency identity through an existing cursor."""
        await cursor.execute(
            f"""
            SELECT {RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.request_key = %s
            """,
            (request_key,),
        )
        row = await cursor.fetchone()
        return run_from_row(row) if row is not None else None

    async def fetch_active_run(
        self,
        cursor: AsyncCursor[DictRow],
        thread_id: UUID,
    ) -> ConversationRun | None:
        """Return a thread's active run through an existing cursor."""
        await cursor.execute(
            f"""
            SELECT {RUN_COLUMNS}
            FROM conversation_responses r
            JOIN conversation_queries q
              ON q.conversation_query_id = r.conversation_query_id
            WHERE r.conversation_thread_id = %s
              AND r.status = 'in_progress'
            """,
            (thread_id,),
        )
        row = await cursor.fetchone()
        return run_from_row(row) if row is not None else None

    async def fetch_artifacts(
        self,
        cursor: AsyncCursor[DictRow],
        run_id: UUID,
    ) -> tuple[StoredArtifact, ...]:
        """Return ordered artifacts through an existing cursor."""
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
                   failure,
                   created_at
            FROM conversation_artifacts
            WHERE conversation_response_id = %s
            ORDER BY ordinal
            """,
            (run_id,),
        )
        return tuple(artifact_from_row(row) for row in await cursor.fetchall())
