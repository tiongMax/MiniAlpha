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
                return AIMessage(
                    content="Apple has a 31.7% operating margin."
                )

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
