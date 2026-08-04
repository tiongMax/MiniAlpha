"""Tests for durable streaming run orchestration."""

import asyncio
from uuid import uuid4

from app.persistence.memory import InMemoryConversationRepository
from app.services.research_agent import (
    AgentStreamComplete,
    AgentStreamEvent,
    ExecutedToolCall,
    ResearchExecutionError,
    ResearchResult,
)
from app.services.thread_research import ThreadResearchService


class StreamingAgent:
    """Agent double producing one complete streamed research turn."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_thread(self, message, **context):
        self.calls += 1
        yield AgentStreamEvent("message_chunk", {"delta": "Final answer."})
        yield AgentStreamEvent(
            "tool_call",
            {
                "tool_call_id": "call-aapl",
                "name": "get_company_overview",
                "arguments": {"symbol": "AAPL"},
            },
        )
        artifact = {
            "artifact_type": "company_overview",
            "schema_version": 1,
            "status": "ok",
            "data": {"symbol": "AAPL"},
        }
        yield AgentStreamEvent("artifact", artifact)
        yield AgentStreamComplete(
            ResearchResult(
                answer="Final answer.",
                tool_calls=(
                    ExecutedToolCall(
                        name="get_company_overview",
                        arguments={"symbol": "AAPL"},
                    ),
                ),
                tool_results=(),
                artifacts=(artifact,),
                checkpoint_id="checkpoint-1",
            )
        )


class RecordingRepository(InMemoryConversationRepository):
    """Repository double exposing when terminal persistence completed."""

    def __init__(self) -> None:
        super().__init__()
        self.committed = False

    async def complete_run(self, *args, **kwargs):
        result = await super().complete_run(*args, **kwargs)
        self.committed = True
        return result


class FailingStreamingAgent:
    """Agent double that fails after the SSE response has begun."""

    async def stream_thread(self, message, **context):
        if False:
            yield
        raise ResearchExecutionError("internal provider detail")


def test_run_end_follows_terminal_commit_and_replay_does_not_execute() -> None:
    """Verify the principal Phase 6 lifecycle and idempotency guarantees."""

    async def exercise():
        repository = RecordingRepository()
        agent = StreamingAgent()
        service = ThreadResearchService(repository, agent)
        request_key = uuid4()
        prepared = await service.prepare_stream(
            "Analyze Apple.", thread_id=None, request_key=request_key
        )
        events = []
        async for event in service.stream(prepared):
            if event.event == "run_end":
                assert repository.committed is True
            events.append(event)
        replay = await service.prepare_stream(
            "Analyze Apple.",
            thread_id=prepared.admission.run.thread_id,
            request_key=request_key,
        )
        replay_events = [event async for event in service.stream(replay)]
        return repository, agent, events, replay_events

    repository, agent, events, replay_events = asyncio.run(exercise())
    assert repository.committed is True
    assert events[0].event == "metadata"
    assert events[-1].event == "run_end"
    assert events[-1].data["status"] == "completed"
    assert replay_events[0].data["replayed"] is True
    assert replay_events[-1].event == "run_end"
    assert agent.calls == 1


def test_stream_failure_is_sanitized_and_persisted() -> None:
    """Verify post-header execution failures close through the event protocol."""

    async def exercise():
        repository = InMemoryConversationRepository()
        service = ThreadResearchService(repository, FailingStreamingAgent())
        prepared = await service.prepare_stream(
            "Analyze Apple.", thread_id=None, request_key=uuid4()
        )
        events = [event async for event in service.stream(prepared)]
        stored = await repository.get_turn(prepared.admission.run.run_id)
        return events, stored

    events, stored = asyncio.run(exercise())
    assert [event.event for event in events] == ["metadata", "error", "run_end"]
    assert events[1].data == {
        "code": "research_failed",
        "message": "The research agent could not complete the request.",
    }
    assert stored is not None
    assert stored.run.status == "error"
