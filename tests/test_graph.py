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


class ScriptedToolCallingModel:
    """A credential-free model double that performs one tool round."""

    def bind_tools(self, _tools):
        async def respond(messages):
            assert isinstance(messages[0], SystemMessage)

            if isinstance(messages[-1], ToolMessage):
                return AIMessage(
                    content="Apple has a 31.7% sample operating margin."
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
    graph = build_graph(cast(BaseChatModel, ScriptedToolCallingModel()))

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
    assert messages[3].content == "Apple has a 31.7% sample operating margin."
    assert not any(isinstance(message, SystemMessage) for message in messages)

