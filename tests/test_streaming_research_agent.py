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


class BlockedStreamingGraph:
    """Graph double that produces nothing until explicitly released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def astream(self, input, config=None, *, stream_mode):
        await self.release.wait()
        if False:
            yield "values", input


def test_planning_progress_precedes_slow_model_output() -> None:
    """The client gets useful state before a model invocation can stall."""

    async def exercise():
        graph = BlockedStreamingGraph()
        service = ResearchAgentService(cast(ResearchGraph, graph))
        stream = service.stream_thread(
            "Run broad research.",
            thread_id=uuid4(),
            run_id=uuid4(),
            checkpoint_id=None,
        )
        first = await asyncio.wait_for(anext(stream), timeout=0.1)
        await stream.aclose()
        return first

    first = asyncio.run(exercise())
    assert isinstance(first, AgentStreamEvent)
    assert first.event == "progress"
    assert first.data["phase"] == "planning"


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
        "progress",
        "progress",
        "tool_call",
        "tool_result",
        "artifact",
        "progress",
        "message_chunk",
        "message_chunk",
    ]
    assert [event.data["phase"] for event in events if event.event == "progress"] == [
        "planning",
        "running_tools",
        "synthesizing",
    ]
    assert events[2].data["tool_call_id"] == "call-aapl"
    assert events[3].data["tool_call_id"] == "call-aapl"
    assert complete.result.answer == "Apple is profitable."
    assert complete.result.checkpoint_id == "checkpoint-streamed"
