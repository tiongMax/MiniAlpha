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

from app.agent.errors import ModelInvocationTimeout
from app.agent.intent_router import IntentRoute, IntentRouter
from app.agent.nodes import route_after_model
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.retry import is_transient_model_error
from app.agent.state import ResearchState, RoutingState
from app.agent.tool_executor import IsolatedToolExecutor
from app.agent.tool_registry import ToolRegistry
from app.agent.tools import create_default_tools
from app.observability import observe_span


def build_graph(
    model: BaseChatModel,
    *,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    model_timeout_seconds: float = 60.0,
    tool_timeout_seconds: float = 30.0,
    model_max_attempts: int = 2,
    tool_max_attempts: int = 2,
    enable_intent_routing: bool = True,
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
        enable_intent_routing: Select a request-scoped tool subset before model
            invocation. Disable only for paired fixed-tool baseline evaluation.

    Returns:
        A compiled LangGraph runnable with the topology
        ``START -> route_tools -> model -> tools -> model -> END``.
    """
    if model_max_attempts <= 0 or tool_max_attempts <= 0:
        raise ValueError("Model and tool attempts must be positive.")
    graph_tools = list(tools) if tools is not None else list(create_default_tools())
    registry = ToolRegistry(graph_tools)
    intent_router = IntentRouter(registry)
    bound_models: dict[tuple[str, ...], object] = {}
    tool_nodes: dict[tuple[str, ...], IsolatedToolExecutor] = {}

    def route_tools(state: ResearchState) -> dict[str, RoutingState]:
        """Store one inspectable request-scoped tool selection."""
        with observe_span(
            "routing.decision",
            metadata={"routing_enabled": enable_intent_routing},
        ) as span:
            route = (
                intent_router.route(state)
                if enable_intent_routing
                else IntentRoute(
                    intents=("fixed_all",),
                    selected_tool_names=registry.names,
                    mode="fixed_all",
                    confidence=1.0,
                    reason="Fixed-tool baseline exposes every registered tool.",
                )
            )
            span.set_attributes(
                {
                    "outcome": "ok",
                    "routing_mode": route.mode,
                    "intent_count": len(route.intents),
                    "selected_tool_count": len(route.selected_tool_names),
                    "available_tool_count": len(registry.names),
                    "confidence": route.confidence,
                }
            )
        return {"routing": route.to_state()}  # type: ignore[return-value]

    def selected_names(state: ResearchState) -> tuple[str, ...]:
        routing = state.get("routing")
        if routing is None:
            return registry.names
        return tuple(routing["selected_tool_names"])

    def bound_model(names: tuple[str, ...]):
        """Bind schemas once per distinct request-scoped tool subset."""
        runnable = bound_models.get(names)
        if runnable is None:
            runnable = model.bind_tools(list(registry.resolve(names)))
            bound_models[names] = runnable
        return runnable

    def selected_tool_node(names: tuple[str, ...]) -> IsolatedToolExecutor:
        """Create one executor containing exactly the model-visible tools."""
        node = tool_nodes.get(names)
        if node is None:
            from app.agent.retry import RetryPolicy

            node = IsolatedToolExecutor(
                list(registry.resolve(names)),
                timeout_seconds=tool_timeout_seconds,
                retry_policy=RetryPolicy(max_attempts=tool_max_attempts),
            )
            tool_nodes[names] = node
        return node

    async def call_model(state: ResearchState) -> ResearchState:
        """Invoke the tool-bound model with transient system instructions.

        Args:
            state: Current append-only conversation state.

        Returns:
            A state update containing only the new model response. LangGraph's
            message reducer merges it into the existing conversation.
        """
        names = selected_names(state)
        model_messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            *state["messages"],
        ]
        model_with_tools = bound_model(names)
        for attempt in range(1, model_max_attempts + 1):
            try:
                with observe_span(
                    "model.invoke",
                    run_type="llm",
                    metadata={
                        "attempt": attempt,
                        "attempt_budget": model_max_attempts,
                        "selected_tool_count": len(names),
                    },
                ) as span:
                    streamed = None
                    async with asyncio.timeout(model_timeout_seconds):
                        async for chunk in model_with_tools.astream(model_messages):
                            if not isinstance(chunk, (AIMessage, AIMessageChunk)):
                                continue
                            streamed = chunk if streamed is None else streamed + chunk
                    if streamed is None:
                        raise RuntimeError("The model returned no message.")
                    response = (
                        message_chunk_to_message(streamed)
                        if isinstance(streamed, AIMessageChunk)
                        else streamed
                    )
                    usage = response.usage_metadata or {}
                    span.set_attributes(
                        {
                            "outcome": "ok",
                            "input_tokens": int(usage.get("input_tokens", 0) or 0),
                            "output_tokens": int(usage.get("output_tokens", 0) or 0),
                            "total_tokens": int(usage.get("total_tokens", 0) or 0),
                            "requested_tool_call_count": len(response.tool_calls),
                        }
                    )
                    return {"messages": [response]}
            except Exception as error:
                if attempt >= model_max_attempts or not is_transient_model_error(error):
                    if not isinstance(error, TimeoutError):
                        raise
                    timeout_message = (
                        f"The model exceeded its {model_timeout_seconds:g}s deadline."
                    )
                    raise ModelInvocationTimeout(timeout_message) from error
                await asyncio.sleep(0.2 * attempt)
        raise AssertionError("unreachable model retry state")

    async def call_tools(state: ResearchState) -> ResearchState:
        """Run sibling calls with per-call deadlines and structured failures."""
        return await selected_tool_node(selected_names(state)).ainvoke(state)

    builder = StateGraph(ResearchState)
    builder.add_node("route_tools", route_tools)
    builder.add_node("model", call_model)
    builder.add_node("tools", call_tools)

    builder.add_edge(START, "route_tools")
    builder.add_edge("route_tools", "model")
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
