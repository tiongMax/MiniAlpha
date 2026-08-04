"""Tests for transport-neutral graph result extraction."""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.research_agent import (
    ResearchAgentService,
    ResearchExecutionError,
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
    service = ResearchAgentService(SuccessfulGraph())

    result = asyncio.run(service.research("Analyze Apple."))

    assert result.answer == "Apple is highly profitable."
    assert result.tool_calls[0].name == "get_company_overview"
    assert result.tool_calls[0].arguments == {"symbol": "AAPL"}
    assert result.tool_results[0].content == "Apple Inc. (AAPL)"
    assert result.artifacts[0]["data"] == {"symbol": "AAPL"}


def test_rejects_graph_result_without_final_answer() -> None:
    """Verify malformed graph completion is an application-level failure."""
    service = ResearchAgentService(AnswerlessGraph())

    try:
        asyncio.run(service.research("Analyze Apple."))
    except ResearchExecutionError as error:
        assert "without a final answer" in str(error)
    else:
        raise AssertionError("Expected ResearchExecutionError")
