"""Explicit LangGraph construction for the research agent."""

import asyncio
from collections.abc import Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.errors import ModelInvocationTimeout, ToolInvocationTimeout
from app.agent.nodes import route_after_model
from app.agent.orchestration import (
    TOOL_CAPABILITIES,
    validate_tool_arguments,
    validation_error_artifact,
    validation_error_message,
)
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
        """Validate and run one tool step within its configured deadline."""
        try:
            async with asyncio.timeout(tool_timeout_seconds):
                last_message = state["messages"][-1]
                if not isinstance(last_message, AIMessage):
                    return await tool_node.ainvoke(state)

                executable_calls: list[dict[str, object]] = []
                results_by_call_id: dict[str, ToolMessage] = {}
                call_order: list[str] = []
                for index, call in enumerate(last_message.tool_calls):
                    call_id = str(call.get("id") or f"tool-call-{index}")
                    call_order.append(call_id)
                    tool_name = str(call.get("name") or "")
                    raw_arguments = call.get("args")
                    arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
                    if tool_name not in TOOL_CAPABILITIES:
                        executable_calls.append({**call, "id": call_id})
                        continue
                    validation = validate_tool_arguments(tool_name, arguments)
                    if validation.is_valid:
                        executable_calls.append(
                            {
                                **call,
                                "id": call_id,
                                "args": dict(validation.arguments),
                            }
                        )
                        continue
                    results_by_call_id[call_id] = ToolMessage(
                        content=validation_error_message(validation),
                        tool_call_id=call_id,
                        name=tool_name or "unknown",
                        artifact=validation_error_artifact(validation),
                        status="error",
                    )

                if executable_calls:
                    execution_message = last_message.model_copy(
                        update={"tool_calls": executable_calls}
                    )
                    execution_state = {
                        **state,
                        "messages": [*state["messages"][:-1], execution_message],
                    }
                    executed = await tool_node.ainvoke(execution_state)
                    for message in executed["messages"]:
                        if isinstance(message, ToolMessage):
                            results_by_call_id[message.tool_call_id] = message

                return {
                    "messages": [
                        results_by_call_id[call_id]
                        for call_id in call_order
                        if call_id in results_by_call_id
                    ]
                }
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
