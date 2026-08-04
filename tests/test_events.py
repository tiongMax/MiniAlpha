"""Tests for the stable Phase 6 event and SSE contracts."""

import json
from uuid import uuid4

from app.api.sse import encode_sse
from app.events.models import RunEventProducer


def test_event_ids_increase_and_sse_frame_is_valid() -> None:
    """Verify application sequencing and wire framing stay deterministic."""
    producer = RunEventProducer(run_id=uuid4(), thread_id=uuid4())
    first = producer.emit("metadata", {"replayed": False})
    second = producer.emit("message_chunk", {"delta": "Hello\nworld"})

    assert first.event_id == 1
    assert second.event_id == 2
    frame = encode_sse(second)
    lines = frame.strip().splitlines()
    assert lines[0] == "id: 2"
    assert lines[1] == "event: message_chunk"
    payload = json.loads(lines[2].removeprefix("data: "))
    assert payload["data"] == {"delta": "Hello\nworld"}
