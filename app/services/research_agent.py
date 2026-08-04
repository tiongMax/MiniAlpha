"""Application service for executing one stateless research request."""

from dataclasses import dataclass
from typing import Protocol, cast

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from app.agent.content import text_content
from app.agent.state import ResearchState


@dataclass(frozen=True, slots=True)
class ExecutedToolCall:
    """Tool request made by the model during a research run."""

    name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class ExecutedToolResult:
    """Model-readable content and optional artifact returned by one tool."""

    name: str
    content: str
    artifact: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """Transport-neutral result of a completed research run."""

    answer: str
    tool_calls: tuple[ExecutedToolCall, ...]
    tool_results: tuple[ExecutedToolResult, ...]
    artifacts: tuple[dict[str, object], ...]


class ResearchGraph(Protocol):
    """Graph behavior required by the research application service."""

    async def ainvoke(
        self,
        input: ResearchState,
        config: RunnableConfig | None = None,
    ) -> ResearchState:
        """Execute a graph from the supplied state."""
        ...


class ResearchExecutionError(RuntimeError):
    """Raised when the graph cannot produce a final research answer."""


class ResearchAgentService:
    """Execute the graph and translate its messages into stable results."""

    def __init__(
        self,
        graph: ResearchGraph,
        *,
        recursion_limit: int = 12,
    ) -> None:
        """Store a compiled graph and its per-request recursion budget."""
        self._graph = graph
        self._recursion_limit = recursion_limit

    async def research(self, message: str) -> ResearchResult:
        """Run one independent user message through the research graph."""
        try:
            state = await self._graph.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config={"recursion_limit": self._recursion_limit},
            )
        except Exception as error:
            raise ResearchExecutionError(
                "The research agent could not complete the request."
            ) from error

        answer = ""
        tool_calls: list[ExecutedToolCall] = []
        tool_results: list[ExecutedToolResult] = []
        artifacts: list[dict[str, object]] = []

        for graph_message in state["messages"]:
            if isinstance(graph_message, AIMessage):
                for call in graph_message.tool_calls:
                    raw_arguments = call.get("args", {})
                    arguments = (
                        cast(dict[str, object], raw_arguments)
                        if isinstance(raw_arguments, dict)
                        else {}
                    )
                    tool_calls.append(
                        ExecutedToolCall(
                            name=str(call.get("name", "")),
                            arguments=arguments,
                        )
                    )
                if not graph_message.tool_calls:
                    candidate = text_content(graph_message.content).strip()
                    if candidate:
                        answer = candidate

            if isinstance(graph_message, ToolMessage) and isinstance(
                graph_message.artifact,
                dict,
            ):
                artifact = cast(dict[str, object], graph_message.artifact)
                artifacts.append(artifact)
                tool_results.append(
                    ExecutedToolResult(
                        name=graph_message.name or "unknown",
                        content=text_content(graph_message.content),
                        artifact=artifact,
                    )
                )
            elif isinstance(graph_message, ToolMessage):
                tool_results.append(
                    ExecutedToolResult(
                        name=graph_message.name or "unknown",
                        content=text_content(graph_message.content),
                        artifact=None,
                    )
                )

        if not answer:
            raise ResearchExecutionError(
                "The research agent completed without a final answer."
            )

        return ResearchResult(
            answer=answer,
            tool_calls=tuple(tool_calls),
            tool_results=tuple(tool_results),
            artifacts=tuple(artifacts),
        )
