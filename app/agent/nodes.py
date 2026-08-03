"""Node factories and routing for the custom research graph."""

from typing import Literal

from app.agent.state import ResearchState


def route_after_model(state: ResearchState) -> Literal["tools", "__end__"]:
    """Choose the graph edge after a model response.

    Args:
        state: Current research state. Its final message must be the most
            recent model response.

    Returns:
        ``"tools"`` when the model requested at least one tool call;
        otherwise LangGraph's ``"__end__"`` sentinel.
    """
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "__end__"
