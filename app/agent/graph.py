"""Explicit LangGraph construction for the Phase 1 research agent."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.nodes import route_after_model
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import ResearchState
from app.agent.tools import PHASE_ONE_TOOLS


def build_graph(model: BaseChatModel, *, checkpointer: Any = None):
    """Build the model -> tools -> model loop without create_agent()."""
    model_with_tools = model.bind_tools(PHASE_ONE_TOOLS)

    async def call_model(state: ResearchState) -> ResearchState:
        model_messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            *state["messages"],
        ]
        response = await model_with_tools.ainvoke(model_messages)
        return {"messages": [response]}

    builder = StateGraph(ResearchState)
    builder.add_node("model", call_model)
    builder.add_node("tools", ToolNode(PHASE_ONE_TOOLS))

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
