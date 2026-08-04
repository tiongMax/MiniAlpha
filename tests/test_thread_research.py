"""Tests for durable thread research orchestration."""

import asyncio
from uuid import uuid4

import pytest

from app.persistence.memory import InMemoryConversationRepository
from app.services.research_agent import (
    ExecutedToolCall,
    ResearchExecutionError,
    ResearchResult,
)
from app.services.thread_research import (
    ExistingRunInProgressError,
    PersistedRunFailedError,
    ThreadResearchService,
)


def run(coroutine):
    """Execute one service coroutine."""
    return asyncio.run(coroutine)


class SuccessfulAgent:
    """Checkpointed agent double recording execution context."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def research_thread(
        self,
        message,
        *,
        thread_id,
        run_id,
        checkpoint_id,
    ):
        self.calls.append(
            {
                "message": message,
                "thread_id": thread_id,
                "run_id": run_id,
                "checkpoint_id": checkpoint_id,
            }
        )
        return ResearchResult(
            answer=f"Answer {len(self.calls)}",
            tool_calls=(
                ExecutedToolCall(
                    name="get_company_overview",
                    arguments={"symbol": "AAPL"},
                ),
            ),
            tool_results=(),
            artifacts=(
                {
                    "artifact_type": "company_overview",
                    "schema_version": 1,
                    "status": "ok",
                    "data": {"symbol": "AAPL"},
                },
            ),
            checkpoint_id=f"checkpoint-{len(self.calls)}",
        )


class FailingAgent:
    """Agent double that fails every attempted execution."""

    def __init__(self) -> None:
        self.calls = 0

    async def research_thread(self, *args, **kwargs):
        self.calls += 1
        raise ResearchExecutionError(
            "The research agent could not complete the request."
        )


def test_completes_two_turns_from_the_committed_checkpoint() -> None:
    """Verify the orchestrator bridges repository heads into graph runs."""
    repository = InMemoryConversationRepository()
    agent = SuccessfulAgent()
    service = ThreadResearchService(repository, agent)

    first = run(
        service.research(
            "Analyze Apple.",
            thread_id=None,
            request_key=uuid4(),
        )
    )
    second = run(
        service.research(
            "Now compare it with Microsoft.",
            thread_id=first.thread_id,
            request_key=uuid4(),
        )
    )

    assert first.answer == "Answer 1"
    assert first.artifacts[0]["data"] == {"symbol": "AAPL"}
    assert second.turn_index == 2
    assert agent.calls[0]["checkpoint_id"] is None
    assert agent.calls[1]["checkpoint_id"] == "checkpoint-1"
    thread = run(repository.get_thread(first.thread_id))
    assert thread is not None
    assert thread.latest_checkpoint_id == "checkpoint-2"


def test_replays_completed_request_without_executing_agent() -> None:
    """Verify completed retransmissions use stored answer and evidence."""
    repository = InMemoryConversationRepository()
    agent = SuccessfulAgent()
    service = ThreadResearchService(repository, agent)
    request_key = uuid4()
    first = run(
        service.research(
            "Analyze Apple.",
            thread_id=None,
            request_key=request_key,
        )
    )

    replay = run(
        service.research(
            "Analyze Apple.",
            thread_id=first.thread_id,
            request_key=request_key,
        )
    )

    assert replay.replayed is True
    assert replay.run_id == first.run_id
    assert replay.answer == first.answer
    assert replay.artifacts == first.artifacts
    assert len(agent.calls) == 1


def test_rejects_replay_while_original_run_is_active() -> None:
    """Verify retransmission cannot execute an admitted active run twice."""
    repository = InMemoryConversationRepository()
    request_key = uuid4()
    admission = run(
        repository.admit_run(
            thread_id=None,
            message="Analyze Apple.",
            request_key=request_key,
        )
    )
    service = ThreadResearchService(repository, SuccessfulAgent())

    with pytest.raises(ExistingRunInProgressError) as error:
        run(
            service.research(
                "Analyze Apple.",
                thread_id=admission.run.thread_id,
                request_key=request_key,
            )
        )

    assert error.value.run_id == admission.run.run_id


def test_graph_failure_is_persisted_and_replayed() -> None:
    """Verify failed execution becomes one durable terminal result."""
    repository = InMemoryConversationRepository()
    agent = FailingAgent()
    service = ThreadResearchService(repository, agent)
    request_key = uuid4()

    with pytest.raises(ResearchExecutionError):
        run(
            service.research(
                "Analyze Apple.",
                thread_id=None,
                request_key=request_key,
            )
        )

    failed = run(repository.get_run_by_request_key(request_key))
    assert failed is not None
    assert failed.status == "error"
    assert failed.error_code == "research_failed"

    with pytest.raises(PersistedRunFailedError) as replay_error:
        run(
            service.research(
                "Analyze Apple.",
                thread_id=failed.thread_id,
                request_key=request_key,
            )
        )

    assert replay_error.value.run_id == failed.run_id
    assert agent.calls == 1
