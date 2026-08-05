"""Deterministic in-memory conversation repository for tests."""

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

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
    RequestKeyConflictError,
    RunInProgressError,
    RunLifecycleConflictError,
    RunNotFoundError,
    ThreadNotFoundError,
)


def _now() -> datetime:
    """Return a timezone-aware repository timestamp."""
    return datetime.now(UTC)


class InMemoryConversationRepository:
    """Implement durable lifecycle semantics without external services."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._threads: dict[UUID, ConversationThread] = {}
        self._runs: dict[UUID, ConversationRun] = {}
        self._request_keys: dict[UUID, UUID] = {}
        self._artifacts: dict[UUID, tuple[StoredArtifact, ...]] = {}

    async def admit_run(
        self,
        *,
        thread_id: UUID | None,
        message: str,
        request_key: UUID | None,
    ) -> RunAdmission:
        """Create a run while enforcing idempotency and thread ownership."""
        async with self._lock:
            if request_key is not None and request_key in self._request_keys:
                existing = self._runs[self._request_keys[request_key]]
                if (
                    existing.message != message
                    or thread_id is not None
                    and existing.thread_id != thread_id
                ):
                    raise RequestKeyConflictError(
                        "The request key belongs to a different request."
                    )
                thread = self._threads[existing.thread_id]
                return RunAdmission(
                    run=existing,
                    from_checkpoint_id=thread.latest_checkpoint_id,
                    replayed=True,
                )

            now = _now()
            if thread_id is None:
                thread_id = uuid4()
                thread = ConversationThread(
                    thread_id=thread_id,
                    status="in_progress",
                    title=None,
                    latest_checkpoint_id=None,
                    next_turn_index=1,
                    created_at=now,
                    updated_at=now,
                )
                self._threads[thread_id] = thread
            else:
                thread = self._threads.get(thread_id)
                if thread is None:
                    raise ThreadNotFoundError("The research thread was not found.")

            if any(
                run.thread_id == thread_id and run.status == "in_progress"
                for run in self._runs.values()
            ):
                raise RunInProgressError(
                    "The research thread already has an active run."
                )

            run = ConversationRun(
                run_id=uuid4(),
                query_id=uuid4(),
                thread_id=thread_id,
                turn_index=thread.next_turn_index,
                attempt_no=1,
                request_key=request_key,
                status="in_progress",
                message=message,
                answer=None,
                tool_calls=(),
                error_code=None,
                error_message=None,
                started_at=now,
                completed_at=None,
            )
            self._runs[run.run_id] = run
            if request_key is not None:
                self._request_keys[request_key] = run.run_id
            self._threads[thread_id] = replace(
                thread,
                status="in_progress",
                next_turn_index=thread.next_turn_index + 1,
                updated_at=now,
            )
            return RunAdmission(
                run=run,
                from_checkpoint_id=thread.latest_checkpoint_id,
                replayed=False,
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
        """Finalize a run and atomically publish its checkpoint."""
        async with self._lock:
            run = self._require_active_run(run_id)
            thread = self._threads[run.thread_id]
            if thread.latest_checkpoint_id != expected_checkpoint_id:
                raise CheckpointConflictError(
                    "The research thread checkpoint changed during execution."
                )

            now = _now()
            completed = replace(
                run,
                status="completed",
                answer=answer,
                tool_calls=tuple(tool_calls),
                completed_at=now,
            )
            stored_artifacts = tuple(
                self._store_artifact(run_id, ordinal, artifact, now)
                for ordinal, artifact in enumerate(artifacts)
            )
            self._runs[run_id] = completed
            self._artifacts[run_id] = stored_artifacts
            self._threads[run.thread_id] = replace(
                thread,
                status="completed",
                latest_checkpoint_id=checkpoint_id,
                updated_at=now,
            )
            return ConversationTurn(run=completed, artifacts=stored_artifacts)

    async def fail_run(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> ConversationRun:
        """Finalize a failed run while preserving the committed checkpoint."""
        async with self._lock:
            run = self._require_active_run(run_id)
            now = _now()
            failed = replace(
                run,
                status="error",
                error_code=error_code,
                error_message=error_message,
                completed_at=now,
            )
            self._runs[run_id] = failed
            self._threads[run.thread_id] = replace(
                self._threads[run.thread_id],
                status="error",
                updated_at=now,
            )
            return failed

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        partial_answer: str = "",
        tool_calls: Sequence[dict[str, object]] = (),
        artifacts: Sequence[dict[str, object]] = (),
    ) -> ConversationRun:
        """Finalize a cancelled run while preserving the committed checkpoint."""
        async with self._lock:
            run = self._require_active_run(run_id)
            now = _now()
            cancelled = replace(
                run,
                status="cancelled",
                answer=partial_answer or None,
                tool_calls=tuple(tool_calls),
                error_code="cancelled",
                error_message="The research run was cancelled.",
                completed_at=now,
            )
            self._runs[run_id] = cancelled
            self._artifacts[run_id] = tuple(
                self._store_artifact(run_id, ordinal, artifact, now)
                for ordinal, artifact in enumerate(artifacts)
            )
            self._threads[run.thread_id] = replace(
                self._threads[run.thread_id],
                status="cancelled",
                updated_at=now,
            )
            return cancelled

    async def get_thread(self, thread_id: UUID) -> ConversationThread | None:
        """Return one thread from memory."""
        async with self._lock:
            return self._threads.get(thread_id)

    async def list_threads(self, *, limit: int, offset: int) -> ThreadPage:
        """Return a bounded page ordered by activity."""
        async with self._lock:
            ordered = sorted(
                self._threads.values(),
                key=lambda thread: thread.updated_at,
                reverse=True,
            )
            return ThreadPage(
                threads=tuple(ordered[offset : offset + limit]),
                total=len(ordered),
                limit=limit,
                offset=offset,
            )

    async def get_run_by_request_key(
        self,
        request_key: UUID,
    ) -> ConversationRun | None:
        """Resolve an in-memory idempotency key."""
        async with self._lock:
            run_id = self._request_keys.get(request_key)
            return self._runs.get(run_id) if run_id is not None else None

    async def get_turn(self, run_id: UUID) -> ConversationTurn | None:
        """Return one in-memory run and its artifacts."""
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return ConversationTurn(
                run=run,
                artifacts=self._artifacts.get(run_id, ()),
            )

    async def list_turns(self, thread_id: UUID) -> tuple[ConversationTurn, ...]:
        """Return all runs and their artifacts in transcript order."""
        async with self._lock:
            if thread_id not in self._threads:
                raise ThreadNotFoundError("The research thread was not found.")
            runs = sorted(
                (run for run in self._runs.values() if run.thread_id == thread_id),
                key=lambda run: (run.turn_index, run.attempt_no),
            )
            return tuple(
                ConversationTurn(
                    run=run,
                    artifacts=self._artifacts.get(run.run_id, ()),
                )
                for run in runs
            )

    def _require_active_run(self, run_id: UUID) -> ConversationRun:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError("The research run was not found.")
        if run.status != "in_progress":
            raise RunLifecycleConflictError(
                "The research run is already in a terminal state."
            )
        return run

    @staticmethod
    def _store_artifact(
        run_id: UUID,
        ordinal: int,
        artifact: dict[str, object],
        created_at: datetime,
    ) -> StoredArtifact:
        parsed = parse_artifact(artifact)
        return StoredArtifact(
            artifact_id=uuid4(),
            run_id=run_id,
            ordinal=ordinal,
            artifact_type=parsed.artifact_type,
            schema_version=parsed.schema_version,
            status=parsed.status,
            data=parsed.data,
            error=parsed.error,
            created_at=created_at,
        )
