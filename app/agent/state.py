"""State shared by the custom LangGraph nodes."""

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ResearchState(TypedDict):
    """Append-only state passed between research graph nodes.

    Attributes:
        messages: Conversation messages accumulated by LangGraph. The
            ``add_messages`` reducer appends new messages and merges updates by
            message ID instead of replacing the complete conversation.
    """

    messages: Annotated[list[AnyMessage], add_messages]
