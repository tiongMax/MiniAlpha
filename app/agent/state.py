"""State shared by the custom LangGraph nodes."""

from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class RoutingState(TypedDict):
    """Inspectable request-scoped tool routing decision."""

    intents: list[str]
    selected_tool_names: list[str]
    mode: Literal["intent", "no_tools", "fallback_all", "fixed_all"]
    confidence: float
    reason: str


class ResearchState(TypedDict):
    """Append-only state passed between research graph nodes.

    Attributes:
        messages: Conversation messages accumulated by LangGraph. The
            ``add_messages`` reducer appends new messages and merges updates by
            message ID instead of replacing the complete conversation.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    routing: NotRequired[RoutingState]
