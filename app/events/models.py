"""Transport-neutral Phase 6 event envelopes."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

EventName = Literal[
    "metadata",
    "progress",
    "message_chunk",
    "tool_call",
    "tool_result",
    "artifact",
    "error",
    "run_end",
]


@dataclass(frozen=True, slots=True)
class RunEvent:
    """One ordered public event belonging to a durable run."""

    event_id: int
    event: EventName
    run_id: UUID
    thread_id: UUID
    timestamp: datetime
    data: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible application envelope."""
        return {
            "event_id": self.event_id,
            "event": self.event,
            "run_id": str(self.run_id),
            "thread_id": str(self.thread_id),
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


class RunEventProducer:
    """Assign monotonically increasing identities to one run's events."""

    def __init__(self, *, run_id: UUID, thread_id: UUID) -> None:
        self._run_id = run_id
        self._thread_id = thread_id
        self._sequence = 0

    def emit(self, event: EventName, data: dict[str, object]) -> RunEvent:
        """Create the next event in this producer's ordered sequence."""
        self._sequence += 1
        return RunEvent(
            event_id=self._sequence,
            event=event,
            run_id=self._run_id,
            thread_id=self._thread_id,
            timestamp=datetime.now(UTC),
            data=data,
        )
