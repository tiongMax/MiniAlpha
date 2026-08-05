"""Explicit LangGraph construction for the research agent."""

import asyncio
from collections.abc import Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    SystemMessage,
    message_chunk_to_message,
)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.errors import ModelInvocationTimeout, ToolInvocationTimeout
from app.agent.nodes import route_after_model
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import ResearchState
from app.agent.tools import create_default_tools


def build_graph(
    model: BaseChatModel,
    *,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    model_timeout_seconds: float = 60.0,
    tool_timeout_seconds: float = 30.0,
):
    """Compile MiniAlpha's explicit model-tool loop.

    Args:
        model: Chat model that supports ``bind_tools`` and asynchronous
            invocation.
        tools: Optional tools to bind and execute. When omitted, the
            Yahoo-backed production tools are created. Supplying tools enables
            provider-free tests and alternative implementations.
        checkpointer: Optional LangGraph checkpointer used to persist graph
            state between invocations.

    Returns:
        A compiled LangGraph runnable with the topology
        ``START -> model -> tools -> model -> END``.
    """
    graph_tools = list(tools) if tools is not None else list(create_default_tools())
    model_with_tools = model.bind_tools(graph_tools)

    async def call_model(state: ResearchState) -> ResearchState:
        """Invoke the tool-bound model with transient system instructions.

        Args:
            state: Current append-only conversation state.

        Returns:
            A state update containing only the new model response. LangGraph's
            message reducer merges it into the existing conversation.
        """
        model_messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            *state["messages"],
        ]
        try:
            async with asyncio.timeout(model_timeout_seconds):
                streamed: AIMessage | AIMessageChunk | None = None
                async for chunk in model_with_tools.astream(model_messages):
                    if not isinstance(chunk, (AIMessage, AIMessageChunk)):
                        continue
                    streamed = chunk if streamed is None else streamed + chunk
        except TimeoutError as error:
            raise ModelInvocationTimeout(
                f"The model exceeded its {model_timeout_seconds:g}s deadline."
            ) from error
        if streamed is None:
            raise RuntimeError("The model returned no message.")
        response = (
            message_chunk_to_message(streamed)
            if isinstance(streamed, AIMessageChunk)
            else streamed
        )
        return {"messages": [response]}

    tool_node = ToolNode(graph_tools)

    async def call_tools(state: ResearchState) -> ResearchState:
        """Run one tool step within its configured deadline."""
        try:
            async with asyncio.timeout(tool_timeout_seconds):
                return await tool_node.ainvoke(state)
        except TimeoutError as error:
            raise ToolInvocationTimeout(
                f"A tool exceeded its {tool_timeout_seconds:g}s deadline."
            ) from error

    builder = StateGraph(ResearchState)
    builder.add_node("model", call_model)
    builder.add_node("tools", call_tools)

    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model",
        route_after_model,
        {
            "tools": "tools",
            END: END,
        },
    )
    builder.add_edge("tools", "model")

    return builder.compile(checkpointer=checkpointer)
