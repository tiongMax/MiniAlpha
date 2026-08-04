"""Transport-neutral records stored by conversation repositories."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

RunStatus = Literal["in_progress", "completed", "error"]
ThreadStatus = Literal["in_progress", "completed", "error"]


@dataclass(frozen=True, slots=True)
class ConversationThread:
    """Durable conversation metadata and its committed graph head."""

    thread_id: UUID
    status: ThreadStatus
    title: str | None
    latest_checkpoint_id: str | None
    next_turn_index: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationRun:
    """One durable execution attempt for a user query."""

    run_id: UUID
    query_id: UUID
    thread_id: UUID
    turn_index: int
    attempt_no: int
    request_key: UUID | None
    status: RunStatus
    message: str
    answer: str | None
    tool_calls: tuple[dict[str, object], ...]
    error_code: str | None
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """One ordered, versioned artifact belonging to a run."""

    artifact_id: UUID
    run_id: UUID
    ordinal: int
    artifact_type: str
    schema_version: int
    status: Literal["ok", "error"]
    data: dict[str, object] | None
    error: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RunAdmission:
    """Result of idempotently admitting one execution attempt."""

    run: ConversationRun
    from_checkpoint_id: str | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """A query/run pair and the evidence persisted for it."""

    run: ConversationRun
    artifacts: tuple[StoredArtifact, ...]


@dataclass(frozen=True, slots=True)
class ThreadPage:
    """Bounded page of threads and the complete matching count."""

    threads: tuple[ConversationThread, ...]
    total: int
    limit: int
    offset: int
