"""Application service for stateless and checkpointed graph execution."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Literal, Protocol, cast
from uuid import UUID

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StateSnapshot

from app.agent.content import text_content
from app.agent.errors import ModelInvocationTimeout, ToolInvocationTimeout
from app.agent.state import ResearchState
from app.observability import observe_span


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
class ModelUsage:
    """Generation tokens consumed by one request."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ResearchCacheInfo:
    """Cache outcome while retaining the cost of the originating response."""

    status: Literal["miss", "exact_hit", "semantic_hit"]
    origin_usage: ModelUsage | None = None


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """Transport-neutral result of a completed research run."""

    answer: str
    tool_calls: tuple[ExecutedToolCall, ...]
    tool_results: tuple[ExecutedToolResult, ...]
    artifacts: tuple[dict[str, object], ...]
    checkpoint_id: str | None
    usage: ModelUsage = field(default_factory=ModelUsage)
    cache: ResearchCacheInfo | None = None


@dataclass(frozen=True, slots=True)
class CachedResearchResult:
    """A valid cached response and the tier that resolved it."""

    result: ResearchResult
    status: Literal["exact_hit", "semantic_hit"]


@dataclass(frozen=True, slots=True)
class CacheFillReservation:
    """Ownership of an optional cross-process cache fill lock."""

    owner: bool
    token: str | None = None


class ResearchResultCache(Protocol):
    """Fail-open result-cache behavior used by stateless requests only."""

    async def lookup(self, message: str) -> CachedResearchResult | None:
        """Return a valid unexpired match or ``None``."""
        ...

    async def store(self, message: str, result: ResearchResult) -> None:
        """Best-effort store of a successful result."""
        ...

    async def acquire_fill(self, message: str) -> CacheFillReservation:
        """Reserve an origin fill when distributed locking is available."""
        ...

    async def wait_for_fill(self, message: str) -> CachedResearchResult | None:
        """Wait briefly for another owner to populate the result."""
        ...

    async def release_fill(
        self,
        message: str,
        reservation: CacheFillReservation,
    ) -> None:
        """Release a reservation owned by this request."""
        ...


@dataclass(frozen=True, slots=True)
class ResearchExecutionContext:
    """Durable identities and committed checkpoint for one threaded run."""

    thread_id: UUID
    run_id: UUID
    checkpoint_id: str | None


AgentEventName = Literal[
    "progress",
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
        result_cache: ResearchResultCache | None = None,
    ) -> None:
        """Store a compiled graph and its per-request recursion budget."""
        self._graph = graph
        self._recursion_limit = recursion_limit
        self._result_cache = result_cache

    async def research(self, message: str) -> ResearchResult:
        """Run one independent user message through the research graph."""
        with observe_span(
            "mini_alpha.research_run",
            metadata={"mode": "stateless", "streaming": False},
        ) as span:
            try:
                result = await self._research_stateless(message)
            except ResearchExecutionError as error:
                span.mark_error_type(error.code)
                span.set_attributes({"outcome": "error", "failure_code": error.code})
                raise
            span.set_attributes(self._result_span_attributes(result))
            return result

    async def _research_stateless(self, message: str) -> ResearchResult:
        """Resolve a stateless result while the caller owns the root span."""
        reservation: CacheFillReservation | None = None
        if self._result_cache is not None:
            try:
                cached = await self._result_cache.lookup(message)
            except Exception:
                cached = None
            if cached is not None:
                return self._cache_hit_result(cached)
            try:
                reservation = await self._result_cache.acquire_fill(message)
                if not reservation.owner:
                    cached = await self._result_cache.wait_for_fill(message)
                    if cached is not None:
                        return self._cache_hit_result(cached)
                    reservation = None
            except Exception:
                reservation = None

        try:
            result = await self._execute(message, context=None)
            if self._result_cache is not None:
                try:
                    await self._result_cache.store(message, result)
                except Exception:
                    pass
                return replace(
                    result,
                    cache=ResearchCacheInfo(status="miss", origin_usage=result.usage),
                )
            return result
        finally:
            if self._result_cache is not None and reservation is not None:
                try:
                    await self._result_cache.release_fill(message, reservation)
                except Exception:
                    pass

    @staticmethod
    def _cache_hit_result(cached: CachedResearchResult) -> ResearchResult:
        """Return a hit with zero current generation usage."""
        return replace(
            cached.result,
            checkpoint_id=None,
            usage=ModelUsage(),
            cache=ResearchCacheInfo(
                status=cached.status,
                origin_usage=cached.result.usage,
            ),
        )

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
        phase = "planning"

        yield AgentStreamEvent(
            event="progress",
            data={
                "phase": phase,
                "message": "Planning research and selecting data tools…",
            },
        )

        try:
            async for mode, payload in self._graph.astream(
                {"messages": [human_message]},
                config=config,
                stream_mode=["messages", "values"],
            ):
                if mode == "messages":
                    chunk_event = self._message_chunk_event(payload)
                    if chunk_event is not None:
                        if phase != "synthesizing":
                            phase = "synthesizing"
                            yield AgentStreamEvent(
                                event="progress",
                                data={
                                    "phase": phase,
                                    "message": "Writing the research answer…",
                                },
                            )
                        yield chunk_event
                    continue
                if mode != "values" or not isinstance(payload, dict):
                    continue

                state = cast(ResearchState, payload)
                final_state = state
                messages = self._messages_after_marker(state["messages"], marker_id)
                new_results = False
                for graph_message in messages:
                    if isinstance(graph_message, AIMessage):
                        new_calls = [
                            call
                            for index, call in enumerate(graph_message.tool_calls)
                            if str(
                                call.get("id")
                                or f"{graph_message.id or 'message'}:{index}"
                            )
                            not in seen_tool_calls
                        ]
                        if new_calls:
                            phase = "running_tools"
                            yield AgentStreamEvent(
                                event="progress",
                                data={
                                    "phase": phase,
                                    "message": (
                                        f"Running {len(new_calls)} financial "
                                        f"{'tool' if len(new_calls) == 1 else 'tools'}…"
                                    ),
                                    "tools": [
                                        str(call.get("name", "")) for call in new_calls
                                    ],
                                },
                            )
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
                    new_results = True
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
                if new_results and phase == "running_tools":
                    phase = "synthesizing"
                    yield AgentStreamEvent(
                        event="progress",
                        data={
                            "phase": phase,
                            "message": (
                                "Reviewing tool results and preparing the answer…"
                            ),
                        },
                    )
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
                usage=result.usage,
                cache=result.cache,
            )
        )

    @staticmethod
    def _result_span_attributes(result: ResearchResult) -> dict[str, object]:
        """Return aggregate, content-free attribution for a completed run."""
        return {
            "outcome": "ok",
            "cache_status": result.cache.status if result.cache is not None else "none",
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
            "tool_call_count": len(result.tool_calls),
            "tool_error_count": sum(
                call.status == "error" for call in result.tool_calls
            ),
            "artifact_count": len(result.artifacts),
        }

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
            usage=result.usage,
            cache=result.cache,
        )

    def _extract_result(self, messages: list[AnyMessage]) -> ResearchResult:
        """Translate graph messages into the stable application result."""
        answer = ""
        tool_calls: list[ExecutedToolCall] = []
        tool_results: list[ExecutedToolResult] = []
        artifacts: list[dict[str, object]] = []
        call_positions: dict[str, int] = {}
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0

        for graph_message in messages:
            if isinstance(graph_message, AIMessage):
                usage = graph_message.usage_metadata or {}
                input_tokens += self._usage_value(usage, "input_tokens")
                output_tokens += self._usage_value(usage, "output_tokens")
                total_tokens += self._usage_value(usage, "total_tokens")
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
            usage=ModelUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
        )

    @staticmethod
    def _usage_value(usage: object, key: str) -> int:
        """Read a non-negative LangChain usage counter defensively."""
        if not isinstance(usage, dict):
            return 0
        value = usage.get(key, 0)
        return value if isinstance(value, int) and value >= 0 else 0

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
