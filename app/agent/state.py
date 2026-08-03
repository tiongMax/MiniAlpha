"""State shared by the Phase 1 graph nodes."""

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ResearchState(TypedDict):
    """The smallest useful agent state: an append-only message conversation."""

    messages: Annotated[list[AnyMessage], add_messages]

