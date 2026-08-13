"""End-to-end test for the explicit model -> tools -> model graph."""

import asyncio
from typing import cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableLambda

from app.agent.graph import build_graph
from app.agent.tools import create_company_overview_tool
from app.services.research_agent import ResearchAgentService, ResearchExecutionError
from tests.test_tools import SuccessfulService


class ScriptedToolCallingModel:
    """A credential-free model double that performs one tool round."""

    def bind_tools(self, _tools):
        """Return a deterministic runnable that simulates one tool round.

        Args:
            _tools: Tools bound by graph composition; unused by this double.

        Returns:
            Async runnable that requests AAPL and then produces a final answer.
        """

        async def respond(messages):
            """Return the next scripted model message.

            Args:
                messages: System prompt followed by current conversation.

            Returns:
                Tool-calling response initially and a final response after the
                tool result.
            """
            assert isinstance(messages[0], SystemMessage)

            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="Apple has a 31.7% operating margin.")

            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-aapl",
                        "name": "get_company_overview",
                        "args": {"symbol": "AAPL"},
                        "type": "tool_call",
                    }
                ],
            )

        return RunnableLambda(respond)


def test_graph_executes_tool_then_returns_final_answer() -> None:
    """Verify the complete model-to-tool-to-model message sequence."""
    graph = build_graph(
        cast(BaseChatModel, ScriptedToolCallingModel()),
        tools=[create_company_overview_tool(SuccessfulService())],
    )

    result = asyncio.run(
        graph.ainvoke(
            {
                "messages": [
                    HumanMessage(content="Analyze Apple."),
                ]
            },
            config={"recursion_limit": 8},
        )
    )

    messages = result["messages"]

    assert len(messages) == 4
    assert isinstance(messages[0], HumanMessage)
    assert isinstance(messages[1], AIMessage)
    assert messages[1].tool_calls[0]["name"] == "get_company_overview"
    assert isinstance(messages[2], ToolMessage)
    assert "Apple Inc. (AAPL)" in str(messages[2].content)
    assert isinstance(messages[3], AIMessage)
    assert messages[3].content == "Apple has a 31.7% operating margin."
    assert not any(isinstance(message, SystemMessage) for message in messages)


class RecordingFinalModel:
    """Record the request-scoped schemas bound before returning a final answer."""

    def __init__(self) -> None:
        self.bound_tool_names: list[tuple[str, ...]] = []

    def bind_tools(self, tools):
        self.bound_tool_names.append(tuple(tool.name for tool in tools))

        async def respond(_messages):
            return AIMessage(content="Done.")

        return RunnableLambda(respond)


def test_graph_binds_only_request_relevant_tools() -> None:
    from langchain_core.tools import tool

    @tool
    async def get_company_news(symbol: str) -> str:
        """Return synthetic company news."""
        return symbol

    @tool
    async def calculate_volatility(symbol: str) -> str:
        """Return synthetic volatility."""
        return symbol

    model = RecordingFinalModel()
    graph = build_graph(
        cast(BaseChatModel, model),
        tools=[get_company_news, calculate_volatility],
    )

    result = asyncio.run(
        graph.ainvoke({"messages": [HumanMessage(content="Show AAPL news.")]})
    )

    assert model.bound_tool_names == [("get_company_news",)]
    assert result["routing"]["selected_tool_names"] == ["get_company_news"]
    assert result["routing"]["mode"] == "intent"


def test_graph_fixed_baseline_binds_every_tool() -> None:
    from langchain_core.tools import tool

    @tool
    async def get_company_news(symbol: str) -> str:
        """Return synthetic company news."""
        return symbol

    @tool
    async def calculate_volatility(symbol: str) -> str:
        """Return synthetic volatility."""
        return symbol

    model = RecordingFinalModel()
    graph = build_graph(
        cast(BaseChatModel, model),
        tools=[get_company_news, calculate_volatility],
        enable_intent_routing=False,
    )

    result = asyncio.run(
        graph.ainvoke({"messages": [HumanMessage(content="Show AAPL news.")]})
    )

    assert model.bound_tool_names == [("get_company_news", "calculate_volatility")]
    assert result["routing"]["mode"] == "fixed_all"


class SlowModel:
    """Model double that exceeds a short execution deadline."""

    def bind_tools(self, _tools):
        async def respond(_messages):
            await asyncio.sleep(1)
            return AIMessage(content="Too late.")

        return RunnableLambda(respond)


def test_model_invocation_timeout_is_controlled() -> None:
    graph = build_graph(
        cast(BaseChatModel, SlowModel()),
        tools=[],
        model_timeout_seconds=0.001,
    )

    try:
        asyncio.run(ResearchAgentService(graph).research("Analyze Apple."))
    except ResearchExecutionError as error:
        assert error.code == "model_timeout"
    else:
        raise AssertionError("Expected ResearchExecutionError")


class TransientModel:
    """Time out once, then complete within the retry budget."""

    def __init__(self) -> None:
        self.attempts = 0

    def bind_tools(self, _tools):
        async def respond(_messages):
            self.attempts += 1
            if self.attempts == 1:
                await asyncio.sleep(0.05)
            return AIMessage(content="Recovered answer.")

        return RunnableLambda(respond)


def test_transient_model_timeout_retries_once() -> None:
    model = TransientModel()
    graph = build_graph(
        cast(BaseChatModel, model),
        tools=[],
        model_timeout_seconds=0.01,
        model_max_attempts=2,
    )

    result = asyncio.run(ResearchAgentService(graph).research("Explain volatility."))

    assert result.answer == "Recovered answer."
    assert model.attempts == 2


class RateLimitedModel:
    """Raise an SDK-shaped rate-limit error once, then recover."""

    def __init__(self) -> None:
        self.attempts = 0

    def bind_tools(self, _tools):
        async def respond(_messages):
            self.attempts += 1
            if self.attempts == 1:
                error = RuntimeError("redacted provider failure")
                error.status_code = 429  # type: ignore[attr-defined]
                raise error
            return AIMessage(content="Recovered after rate limiting.")

        return RunnableLambda(respond)


def test_transient_model_rate_limit_retries_once() -> None:
    model = RateLimitedModel()
    graph = build_graph(
        cast(BaseChatModel, model),
        tools=[],
        model_max_attempts=2,
    )

    result = asyncio.run(ResearchAgentService(graph).research("Explain volatility."))

    assert result.answer == "Recovered after rate limiting."
    assert model.attempts == 2


class ToolCallingModel:
    """Model double that requests one deliberately slow tool."""

    def bind_tools(self, _tools):
        async def respond(messages):
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="Done.")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "slow-call",
                        "name": "slow_tool",
                        "args": {},
                        "type": "tool_call",
                    }
                ],
            )

        return RunnableLambda(respond)


def test_tool_invocation_timeout_becomes_reasonable_error_artifact() -> None:
    from langchain_core.tools import tool

    @tool
    async def slow_tool() -> str:
        """Wait long enough to exceed the test deadline."""
        await asyncio.sleep(1)
        return "Too late."

    graph = build_graph(
        cast(BaseChatModel, ToolCallingModel()),
        tools=[slow_tool],
        tool_timeout_seconds=0.001,
    )

    result = asyncio.run(ResearchAgentService(graph).research("Analyze Apple."))

    assert result.answer == "Done."
    assert result.tool_calls[0].status == "error"
    assert result.artifacts[0]["failure"]["code"] == "tool_timeout"
