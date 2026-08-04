"""Tests for translating LangGraph streams into application updates."""

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.services.research_agent import (
    AgentStreamComplete,
    AgentStreamEvent,
    ResearchAgentService,
    ResearchGraph,
)


class StreamingGraph:
    """Deterministic graph double with text, tool, and artifact updates."""

    async def astream(self, input, config=None, *, stream_mode):
        human = input["messages"][0]
        tool_call = AIMessage(
            content="",
            id="assistant-tool",
            tool_calls=[
                {
                    "id": "call-aapl",
                    "name": "get_company_overview",
                    "args": {"symbol": "AAPL"},
                    "type": "tool_call",
                }
            ],
        )
        artifact = {
            "artifact_type": "company_overview",
            "schema_version": 1,
            "status": "ok",
            "data": {"symbol": "AAPL"},
        }
        tool_result = ToolMessage(
            content="Apple Inc. (AAPL)",
            tool_call_id="call-aapl",
            name="get_company_overview",
            artifact=artifact,
        )
        final = AIMessage(content="Apple is profitable.")

        assert stream_mode == ["messages", "values"]
        yield "values", {"messages": [human]}
        yield "values", {"messages": [human, tool_call]}
        yield "values", {"messages": [human, tool_call, tool_result]}
        yield (
            "messages",
            (
                AIMessageChunk(content="Apple is "),
                {"langgraph_node": "model"},
            ),
        )
        yield (
            "messages",
            (
                AIMessageChunk(content="profitable."),
                {"langgraph_node": "model"},
            ),
        )
        yield "values", {"messages": [human, tool_call, tool_result, final]}

    async def aget_state(self, config, *, subgraphs=False):
        return SimpleNamespace(
            config={
                "configurable": {
                    **config["configurable"],
                    "checkpoint_id": "checkpoint-streamed",
                }
            }
        )


def test_streams_stable_updates_and_terminal_result() -> None:
    """Verify graph internals are deduplicated and accumulated consistently."""

    async def collect():
        service = ResearchAgentService(cast(ResearchGraph, StreamingGraph()))
        return [
            item
            async for item in service.stream_thread(
                "Analyze Apple.",
                thread_id=uuid4(),
                run_id=uuid4(),
                checkpoint_id=None,
            )
        ]

    items = asyncio.run(collect())
    events = [item for item in items if isinstance(item, AgentStreamEvent)]
    complete = next(item for item in items if isinstance(item, AgentStreamComplete))

    assert [event.event for event in events] == [
        "tool_call",
        "tool_result",
        "artifact",
        "message_chunk",
        "message_chunk",
    ]
    assert events[0].data["tool_call_id"] == "call-aapl"
    assert events[1].data["tool_call_id"] == "call-aapl"
    assert complete.result.answer == "Apple is profitable."
    assert complete.result.checkpoint_id == "checkpoint-streamed"
