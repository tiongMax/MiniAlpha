"""Application service for stateless and checkpointed graph execution."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import UUID

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StateSnapshot

from app.agent.content import text_content
from app.agent.errors import ModelInvocationTimeout, ToolInvocationTimeout
from app.agent.state import ResearchState


@dataclass(frozen=True, slots=True)
class ExecutedToolCall:
    """Tool request made by the model during a research run."""

    name: str
    arguments: dict[str, object]
    status: Literal["ok", "error"] | None = None
    summary: str | None = None


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
    checkpoint_id: str | None


@dataclass(frozen=True, slots=True)
class ResearchExecutionContext:
    """Durable identities and committed checkpoint for one threaded run."""

    thread_id: UUID
    run_id: UUID
    checkpoint_id: str | None


AgentEventName = Literal[
    "message_chunk",
    "tool_call",
    "tool_result",
    "artifact",
]


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    """One transport-neutral live update translated from LangGraph."""

    event: AgentEventName
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class AgentStreamComplete:
    """Private terminal item carrying the authoritative graph result."""

    result: ResearchResult


AgentStreamItem = AgentStreamEvent | AgentStreamComplete


class ResearchGraph(Protocol):
    """Graph behavior required by the research application service."""

    async def ainvoke(
        self,
        input: ResearchState,
        config: RunnableConfig | None = None,
    ) -> ResearchState:
        """Execute a graph from the supplied state."""
        ...

    def astream(
        self,
        input: ResearchState,
        config: RunnableConfig | None = None,
        *,
        stream_mode: list[str],
    ) -> AsyncIterator[tuple[str, object]]:
        """Stream graph messages and state values."""
        ...


class CheckpointedResearchGraph(ResearchGraph, Protocol):
    """Graph behavior additionally required for durable thread execution."""

    async def aget_state(
        self,
        config: RunnableConfig,
        *,
        subgraphs: bool = False,
    ) -> StateSnapshot:
        """Return the checkpointed state selected by the supplied config."""
        ...


class ResearchExecutionError(RuntimeError):
    """Raised when the graph cannot produce a final research answer."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "research_failed",
    ) -> None:
        super().__init__(message)
        self.code = code


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
        return await self._execute(message, context=None)

    async def research_thread(
        self,
        message: str,
        *,
        thread_id: UUID,
        run_id: UUID,
        checkpoint_id: str | None,
    ) -> ResearchResult:
        """Run one message against a durable conversation checkpoint."""
        return await self._execute(
            message,
            context=ResearchExecutionContext(
                thread_id=thread_id,
                run_id=run_id,
                checkpoint_id=checkpoint_id,
            ),
        )

    async def stream_thread(
        self,
        message: str,
        *,
        thread_id: UUID,
        run_id: UUID,
        checkpoint_id: str | None,
    ) -> AsyncIterator[AgentStreamItem]:
        """Stream one checkpointed turn and finish with its stable result."""
        context = ResearchExecutionContext(
            thread_id=thread_id,
            run_id=run_id,
            checkpoint_id=checkpoint_id,
        )
        config = self._execution_config(context)
        marker_id = f"run:{run_id}"
        human_message = HumanMessage(content=message, id=marker_id)
        final_state: ResearchState | None = None
        seen_tool_calls: set[str] = set()
        seen_tool_results: set[str] = set()

        try:
            async for mode, payload in self._graph.astream(
                {"messages": [human_message]},
                config=config,
                stream_mode=["messages", "values"],
            ):
                if mode == "messages":
                    chunk_event = self._message_chunk_event(payload)
                    if chunk_event is not None:
                        yield chunk_event
                    continue
                if mode != "values" or not isinstance(payload, dict):
                    continue

                state = cast(ResearchState, payload)
                final_state = state
                messages = self._messages_after_marker(state["messages"], marker_id)
                for graph_message in messages:
                    if isinstance(graph_message, AIMessage):
                        for index, call in enumerate(graph_message.tool_calls):
                            call_id = str(
                                call.get("id")
                                or f"{graph_message.id or 'message'}:{index}"
                            )
                            if call_id in seen_tool_calls:
                                continue
                            raw_arguments = call.get("args", {})
                            arguments = (
                                cast(dict[str, object], raw_arguments)
                                if isinstance(raw_arguments, dict)
                                else {}
                            )
                            seen_tool_calls.add(call_id)
                            yield AgentStreamEvent(
                                event="tool_call",
                                data={
                                    "tool_call_id": call_id,
                                    "name": str(call.get("name", "")),
                                    "arguments": arguments,
                                },
                            )
                    if not isinstance(graph_message, ToolMessage):
                        continue
                    call_id = graph_message.tool_call_id
                    if call_id in seen_tool_results:
                        continue
                    seen_tool_results.add(call_id)
                    artifact = (
                        cast(dict[str, object], graph_message.artifact)
                        if isinstance(graph_message.artifact, dict)
                        else None
                    )
                    status = (
                        str(artifact.get("status", "ok"))
                        if artifact is not None
                        else "ok"
                    )
                    yield AgentStreamEvent(
                        event="tool_result",
                        data={
                            "tool_call_id": call_id,
                            "name": graph_message.name or "unknown",
                            "status": status,
                            "summary": text_content(graph_message.content),
                        },
                    )
                    if artifact is not None:
                        yield AgentStreamEvent(event="artifact", data=artifact)
        except ResearchExecutionError:
            raise
        except ModelInvocationTimeout as error:
            raise ResearchExecutionError(str(error), code="model_timeout") from error
        except ToolInvocationTimeout as error:
            raise ResearchExecutionError(str(error), code="tool_timeout") from error
        except Exception as error:
            raise ResearchExecutionError(
                "The research agent could not complete the request."
            ) from error

        if final_state is None:
            raise ResearchExecutionError(
                "The research agent completed without a final state."
            )
        current_messages = self._messages_after_marker(
            final_state["messages"],
            marker_id,
        )
        result = self._extract_result(current_messages)
        checkpoint = await self._result_checkpoint_id(context)
        yield AgentStreamComplete(
            result=ResearchResult(
                answer=result.answer,
                tool_calls=result.tool_calls,
                tool_results=result.tool_results,
                artifacts=result.artifacts,
                checkpoint_id=checkpoint,
            )
        )

    async def _execute(
        self,
        message: str,
        *,
        context: ResearchExecutionContext | None,
    ) -> ResearchResult:
        """Invoke the graph and translate only this execution's messages."""
        config = self._execution_config(context)
        marker_id = f"run:{context.run_id}" if context is not None else None
        human_message = HumanMessage(content=message, id=marker_id)
        try:
            state = await self._graph.ainvoke(
                {"messages": [human_message]},
                config=config,
            )
        except ModelInvocationTimeout as error:
            raise ResearchExecutionError(str(error), code="model_timeout") from error
        except ToolInvocationTimeout as error:
            raise ResearchExecutionError(str(error), code="tool_timeout") from error
        except Exception as error:
            raise ResearchExecutionError(
                "The research agent could not complete the request."
            ) from error

        messages = state["messages"]
        current_messages = (
            self._messages_after_marker(messages, marker_id)
            if marker_id is not None
            else messages
        )
        result = self._extract_result(current_messages)
        checkpoint_id = (
            await self._result_checkpoint_id(context) if context is not None else None
        )
        return ResearchResult(
            answer=result.answer,
            tool_calls=result.tool_calls,
            tool_results=result.tool_results,
            artifacts=result.artifacts,
            checkpoint_id=checkpoint_id,
        )

    def _extract_result(self, messages: list[AnyMessage]) -> ResearchResult:
        """Translate graph messages into the stable application result."""
        answer = ""
        tool_calls: list[ExecutedToolCall] = []
        tool_results: list[ExecutedToolResult] = []
        artifacts: list[dict[str, object]] = []
        call_positions: dict[str, int] = {}

        for graph_message in messages:
            if isinstance(graph_message, AIMessage):
                for call in graph_message.tool_calls:
                    raw_arguments = call.get("args", {})
                    arguments = (
                        cast(dict[str, object], raw_arguments)
                        if isinstance(raw_arguments, dict)
                        else {}
                    )
                    call_id = str(call.get("id") or "")
                    if call_id:
                        call_positions[call_id] = len(tool_calls)
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
                self._record_tool_outcome(
                    tool_calls,
                    call_positions,
                    graph_message,
                    artifact_status=artifact.get("status"),
                )
                tool_results.append(
                    ExecutedToolResult(
                        name=graph_message.name or "unknown",
                        content=text_content(graph_message.content),
                        artifact=artifact,
                    )
                )
            elif isinstance(graph_message, ToolMessage):
                self._record_tool_outcome(
                    tool_calls,
                    call_positions,
                    graph_message,
                    artifact_status="ok",
                )
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
            artifacts=self._without_superseded_errors(artifacts),
            checkpoint_id=None,
        )

    @staticmethod
    def _record_tool_outcome(
        calls: list[ExecutedToolCall],
        positions: dict[str, int],
        message: ToolMessage,
        *,
        artifact_status: object,
    ) -> None:
        index = positions.get(message.tool_call_id)
        if index is None:
            return
        existing = calls[index]
        status: Literal["ok", "error"] = "error" if artifact_status == "error" else "ok"
        calls[index] = ExecutedToolCall(
            name=existing.name,
            arguments=existing.arguments,
            status=status,
            summary=text_content(message.content),
        )

    @staticmethod
    def _without_superseded_errors(
        artifacts: list[dict[str, object]],
    ) -> tuple[dict[str, object], ...]:
        """Drop an error artifact when a later retry of its type succeeds."""
        successful_types: set[str] = set()
        kept: list[dict[str, object]] = []
        for artifact in reversed(artifacts):
            artifact_type = artifact.get("artifact_type")
            if not isinstance(artifact_type, str):
                kept.append(artifact)
                continue
            if artifact.get("status") == "ok":
                successful_types.add(artifact_type)
                kept.append(artifact)
            elif artifact_type not in successful_types:
                kept.append(artifact)
        kept.reverse()
        return tuple(kept)

    def _execution_config(
        self,
        context: ResearchExecutionContext | None,
    ) -> RunnableConfig:
        """Build stateless or durable LangGraph execution configuration."""
        config: RunnableConfig = {"recursion_limit": self._recursion_limit}
        if context is None:
            return config

        configurable: dict[str, object] = {
            "thread_id": str(context.thread_id),
            "checkpoint_ns": "",
        }
        if context.checkpoint_id is not None:
            configurable["checkpoint_id"] = context.checkpoint_id
        config["configurable"] = configurable
        config["metadata"] = {
            "run_id": str(context.run_id),
        }
        return config

    @staticmethod
    def _message_chunk_event(payload: object) -> AgentStreamEvent | None:
        """Translate one LangGraph messages-mode payload into assistant text."""
        if not isinstance(payload, tuple) or len(payload) != 2:
            return None
        message, metadata = payload
        if not isinstance(message, AIMessage):
            return None
        if isinstance(metadata, dict):
            node = metadata.get("langgraph_node")
            if node is not None and node != "model":
                return None
        content = text_content(message.content)
        if not content:
            return None
        return AgentStreamEvent(
            event="message_chunk",
            data={"delta": content},
        )

    @staticmethod
    def _messages_after_marker(
        messages: list[AnyMessage],
        marker_id: str,
    ) -> list[AnyMessage]:
        """Return messages generated after this run's human input."""
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].id == marker_id:
                return messages[index + 1 :]
        raise ResearchExecutionError(
            "The research agent result did not contain the current run marker."
        )

    async def _result_checkpoint_id(
        self,
        context: ResearchExecutionContext,
    ) -> str:
        """Read the newest root checkpoint produced by a successful run."""
        graph = cast(CheckpointedResearchGraph, self._graph)
        lookup_config: RunnableConfig = {
            "configurable": {
                "thread_id": str(context.thread_id),
                "checkpoint_ns": "",
            }
        }
        try:
            snapshot = await graph.aget_state(lookup_config)
            configurable = snapshot.config.get("configurable", {})
            checkpoint_id = configurable.get("checkpoint_id")
        except Exception as error:
            raise ResearchExecutionError(
                "The research agent completed without a readable checkpoint."
            ) from error
        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise ResearchExecutionError(
                "The research agent completed without a readable checkpoint."
            )
        return checkpoint_id
