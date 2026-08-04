"""Psycopg façade for durable conversation lifecycle persistence."""

from collections.abc import Sequence
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg_pool import AsyncConnectionPool

from app.persistence.models import (
    ConversationRun,
    ConversationThread,
    ConversationTurn,
    RunAdmission,
    ThreadPage,
)
from app.persistence.postgres_admission import PostgresRunAdmission
from app.persistence.postgres_lifecycle import PostgresRunLifecycle
from app.persistence.postgres_reader import PostgresConversationReader


class PostgresConversationRepository:
    """Expose one repository while delegating focused PostgreSQL operations."""

    def __init__(
        self,
        pool: AsyncConnectionPool[AsyncConnection[DictRow]],
    ) -> None:
        reader = PostgresConversationReader(pool)
        self._reader = reader
        self._admission = PostgresRunAdmission(pool, reader)
        self._lifecycle = PostgresRunLifecycle(pool, reader)

    async def admit_run(
        self,
        *,
        thread_id: UUID | None,
        message: str,
        request_key: UUID | None,
    ) -> RunAdmission:
        """Atomically allocate a turn or recover an idempotent request."""
        return await self._admission.admit_run(
            thread_id=thread_id,
            message=message,
            request_key=request_key,
        )

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
        return await self._lifecycle.complete_run(
            run_id,
            expected_checkpoint_id=expected_checkpoint_id,
            checkpoint_id=checkpoint_id,
            answer=answer,
            tool_calls=tool_calls,
            artifacts=artifacts,
        )

    async def fail_run(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> ConversationRun:
        """Mark a run failed without publishing a graph checkpoint."""
        return await self._lifecycle.fail_run(
            run_id,
            error_code=error_code,
            error_message=error_message,
        )

    async def get_thread(self, thread_id: UUID) -> ConversationThread | None:
        """Return one PostgreSQL-backed thread."""
        return await self._reader.get_thread(thread_id)

    async def list_threads(self, *, limit: int, offset: int) -> ThreadPage:
        """Return a page of threads ordered by recent activity."""
        return await self._reader.list_threads(limit=limit, offset=offset)

    async def get_run_by_request_key(
        self,
        request_key: UUID,
    ) -> ConversationRun | None:
        """Resolve a globally unique request key."""
        return await self._reader.get_run_by_request_key(request_key)

    async def get_turn(self, run_id: UUID) -> ConversationTurn | None:
        """Return one PostgreSQL run and its ordered artifacts."""
        return await self._reader.get_turn(run_id)

    async def list_turns(self, thread_id: UUID) -> tuple[ConversationTurn, ...]:
        """Return all persisted attempts for one thread."""
        return await self._reader.list_turns(thread_id)
