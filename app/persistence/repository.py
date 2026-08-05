"""Conversation repository protocol and controlled persistence failures."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.persistence.models import (
    ConversationRun,
    ConversationThread,
    ConversationTurn,
    RunAdmission,
    ThreadPage,
)


class ConversationPersistenceError(RuntimeError):
    """Base class for controlled conversation persistence failures."""


class ThreadNotFoundError(ConversationPersistenceError):
    """Raised when a requested thread does not exist."""


class RunNotFoundError(ConversationPersistenceError):
    """Raised when a requested run does not exist."""


class RunInProgressError(ConversationPersistenceError):
    """Raised when a thread already owns an active run."""


class RequestKeyConflictError(ConversationPersistenceError):
    """Raised when an idempotency key is reused for different input."""


class CheckpointConflictError(ConversationPersistenceError):
    """Raised when a stale run attempts to publish a checkpoint head."""


class RunLifecycleConflictError(ConversationPersistenceError):
    """Raised when a run cannot perform the requested state transition."""


class ConversationRepository(Protocol):
    """Storage behavior required by the threaded research service."""

    async def admit_run(
        self,
        *,
        thread_id: UUID | None,
        message: str,
        request_key: UUID | None,
    ) -> RunAdmission:
        """Create or idempotently recover one in-progress run."""
        ...

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
        """Commit one successful run and publish its checkpoint head."""
        ...

    async def fail_run(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> ConversationRun:
        """Mark an active run terminal without moving the checkpoint head."""
        ...

    async def recover_abandoned_runs(self) -> int:
        """Fail runs left active by an earlier process and return their count."""
        ...

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        partial_answer: str = "",
        tool_calls: Sequence[dict[str, object]] = (),
        artifacts: Sequence[dict[str, object]] = (),
    ) -> ConversationRun:
        """Mark an active run cancelled without moving the checkpoint head."""
        ...

    async def get_thread(self, thread_id: UUID) -> ConversationThread | None:
        """Return one thread or ``None`` when it does not exist."""
        ...

    async def list_threads(self, *, limit: int, offset: int) -> ThreadPage:
        """Return threads ordered by most recent activity."""
        ...

    async def get_run_by_request_key(
        self,
        request_key: UUID,
    ) -> ConversationRun | None:
        """Resolve one client idempotency key."""
        ...

    async def get_turn(self, run_id: UUID) -> ConversationTurn | None:
        """Return one run and its ordered artifacts."""
        ...

    async def list_turns(self, thread_id: UUID) -> tuple[ConversationTurn, ...]:
        """Return a thread transcript ordered by turn and attempt."""
        ...
