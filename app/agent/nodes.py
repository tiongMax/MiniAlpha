"""Node factories and routing for the custom research graph."""

from typing import Literal

from app.agent.state import ResearchState


def route_after_model(state: ResearchState) -> Literal["tools", "__end__"]:
    """Continue to tool execution only when the latest model message asks."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "__end__"
