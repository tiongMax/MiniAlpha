"""Tests for transport-neutral graph result extraction."""

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.content import text_content
from app.agent.graph import build_graph
from app.services.research_agent import (
    ResearchAgentService,
    ResearchExecutionError,
    ResearchGraph,
)


class SuccessfulGraph:
    """Graph double returning one complete tool-assisted exchange."""

    async def ainvoke(self, input, config=None):
        """Return deterministic messages while checking execution settings."""
        assert isinstance(input["messages"][0], HumanMessage)
        assert input["messages"][0].content == "Analyze Apple."
        assert config == {"recursion_limit": 12}
        return {
            "messages": [
                input["messages"][0],
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "call-aapl",
                            "name": "get_company_overview",
                            "args": {"symbol": "AAPL"},
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(
                    content="Apple Inc. (AAPL)",
                    tool_call_id="call-aapl",
                    name="get_company_overview",
                    artifact={
                        "artifact_type": "company_overview",
                        "schema_version": 1,
                        "status": "ok",
                        "data": {"symbol": "AAPL"},
                    },
                ),
                AIMessage(
                    content=[
                        {
                            "type": "text",
                            "text": "Apple is highly profitable.",
                        }
                    ]
                ),
            ]
        }


class AnswerlessGraph:
    """Graph double that completes without a final assistant answer."""

    async def ainvoke(self, input, config=None):
        """Return only the original user message."""
        return {"messages": input["messages"]}


def test_extracts_answer_tool_calls_results_and_artifacts() -> None:
    """Verify graph internals become a stable application result."""
    service = ResearchAgentService(cast(ResearchGraph, SuccessfulGraph()))

    result = asyncio.run(service.research("Analyze Apple."))

    assert result.answer == "Apple is highly profitable."
    assert result.tool_calls[0].name == "get_company_overview"
    assert result.tool_calls[0].arguments == {"symbol": "AAPL"}
    assert result.tool_results[0].content == "Apple Inc. (AAPL)"
    assert result.artifacts[0]["data"] == {"symbol": "AAPL"}
    assert result.checkpoint_id is None


def test_rejects_graph_result_without_final_answer() -> None:
    """Verify malformed graph completion is an application-level failure."""
    service = ResearchAgentService(cast(ResearchGraph, AnswerlessGraph()))

    try:
        asyncio.run(service.research("Analyze Apple."))
    except ResearchExecutionError as error:
        assert "without a final answer" in str(error)
    else:
        raise AssertionError("Expected ResearchExecutionError")


class HistoricalGraph:
    """Checkpoint-aware double containing evidence from an earlier turn."""

    def __init__(self) -> None:
        self.config = None

    async def ainvoke(self, input, config=None):
        """Return old messages before the current run marker."""
        self.config = config
        return {
            "messages": [
                HumanMessage(content="Old question"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "old-call",
                            "name": "old_tool",
                            "args": {},
                            "type": "tool_call",
                        }
                    ],
                ),
                input["messages"][0],
                AIMessage(content="Current answer."),
            ]
        }

    async def aget_state(self, config, *, subgraphs=False):
        """Return the newest checkpoint identity for the thread."""
        assert "checkpoint_id" not in config["configurable"]
        return SimpleNamespace(
            config={
                "configurable": {
                    **config["configurable"],
                    "checkpoint_id": "checkpoint-current",
                }
            }
        )


def test_checkpointed_result_contains_only_current_turn() -> None:
    """Verify previous tool calls do not leak into a new response."""
    graph = HistoricalGraph()
    service = ResearchAgentService(cast(ResearchGraph, graph))
    thread_id = uuid4()
    run_id = uuid4()

    result = asyncio.run(
        service.research_thread(
            "Current question.",
            thread_id=thread_id,
            run_id=run_id,
            checkpoint_id="checkpoint-previous",
        )
    )

    assert result.answer == "Current answer."
    assert result.tool_calls == ()
    assert result.checkpoint_id == "checkpoint-current"
    assert graph.config == {
        "recursion_limit": 12,
        "configurable": {
            "thread_id": str(thread_id),
            "checkpoint_ns": "",
            "checkpoint_id": "checkpoint-previous",
        },
        "metadata": {"run_id": str(run_id)},
    }


class RememberingModel:
    """Credential-free model that reports earlier user-message content."""

    def bind_tools(self, _tools):
        """Return a runnable final-answer model."""

        async def respond(messages):
            human_messages = [
                text_content(message.content)
                for message in messages
                if isinstance(message, HumanMessage)
            ]
            return AIMessage(content=" | ".join(human_messages))

        return RunnableLambda(respond)


def test_in_memory_checkpointer_preserves_thread_context() -> None:
    """Verify a second graph turn remembers the first after checkpointing."""
    graph = build_graph(
        cast(BaseChatModel, RememberingModel()),
        tools=[],
        checkpointer=InMemorySaver(),
    )
    service = ResearchAgentService(cast(ResearchGraph, graph))
    thread_id = uuid4()

    first = asyncio.run(
        service.research_thread(
            "Analyze Apple.",
            thread_id=thread_id,
            run_id=uuid4(),
            checkpoint_id=None,
        )
    )
    second = asyncio.run(
        service.research_thread(
            "Now compare it with Microsoft.",
            thread_id=thread_id,
            run_id=uuid4(),
            checkpoint_id=first.checkpoint_id,
        )
    )

    assert first.checkpoint_id is not None
    assert second.checkpoint_id is not None
    assert second.answer == "Analyze Apple. | Now compare it with Microsoft."
