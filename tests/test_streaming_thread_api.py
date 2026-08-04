"""Credential-free HTTP tests for Phase 6 SSE routes."""

import json
from uuid import uuid4

from app.api.main import create_app
from app.persistence.memory import InMemoryConversationRepository
from app.services.thread_research import ThreadResearchService
from tests.api_helpers import api_request, research_service
from tests.test_research_agent import SuccessfulGraph
from tests.test_streaming_thread_research import StreamingAgent


def _events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_create_and_continue_stream_routes_emit_stable_sse() -> None:
    """Verify streaming HTTP framing, ordering, and durable continuation."""
    repository = InMemoryConversationRepository()
    agent = StreamingAgent()
    app = create_app(
        research_service(SuccessfulGraph()),
        ThreadResearchService(repository, agent),
    )
    body = {
        "messages": [{"role": "user", "content": "Analyze Apple."}],
        "request_key": str(uuid4()),
    }
    first = api_request(
        app,
        "POST",
        "/api/v1/threads/messages/stream",
        json=body,
    )
    first_events = _events(first.text)
    thread_id = first_events[0]["thread_id"]
    second = api_request(
        app,
        "POST",
        f"/api/v1/threads/{thread_id}/messages/stream",
        json={
            "messages": [{"role": "user", "content": "Compare Microsoft."}],
            "request_key": str(uuid4()),
        },
    )
    second_events = _events(second.text)

    assert first.status_code == 200
    assert first.headers["content-type"].startswith("text/event-stream")
    assert first.headers["x-accel-buffering"] == "no"
    assert first_events[0]["event"] == "metadata"
    assert first_events[-1]["event"] == "run_end"
    assert [event["event_id"] for event in first_events] == list(
        range(1, len(first_events) + 1)
    )
    assert second_events[0]["data"]["turn_index"] == 2
    assert agent.calls == 2
