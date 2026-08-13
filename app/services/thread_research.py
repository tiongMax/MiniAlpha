"""Transport-neutral orchestration for durable research turns."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import UUID

from app.events.models import RunEvent, RunEventProducer
from app.observability import observe_span
from app.persistence.models import (
    ConversationRun,
    ConversationThread,
    ConversationTurn,
    RunAdmission,
    StoredArtifact,
    ThreadPage,
)
from app.persistence.repository import (
    CheckpointConflictError,
    ConversationRepository,
    RunNotFoundError,
    ThreadNotFoundError,
)
from app.services.research_agent import (
    AgentStreamComplete,
    AgentStreamEvent,
    AgentStreamItem,
    ExecutedToolCall,
    ResearchExecutionError,
    ResearchResult,
)


@dataclass(frozen=True, slots=True)
class ThreadResearchResult:
    """Completed durable turn returned independently of HTTP."""

    thread_id: UUID
    run_id: UUID
    turn_index: int
    answer: str
    tool_calls: tuple[ExecutedToolCall, ...]
    artifacts: tuple[dict[str, object], ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class ThreadResearchStream:
    """Pre-admitted stream whose HTTP status can still fail normally."""

    admission: RunAdmission
    replay: ThreadResearchResult | None


class ThreadResearchAgent(Protocol):
    """Checkpointed graph behavior required by the thread orchestrator."""

    async def research_thread(
        self,
        message: str,
        *,
        thread_id: UUID,
        run_id: UUID,
        checkpoint_id: str | None,
    ) -> ResearchResult:
        """Execute one message from a committed thread checkpoint."""
        ...

    def stream_thread(
        self,
        message: str,
        *,
        thread_id: UUID,
        run_id: UUID,
        checkpoint_id: str | None,
    ) -> AsyncIterator[AgentStreamItem]:
        """Stream one message from a committed conversation checkpoint."""
        ...


class ExistingRunInProgressError(RuntimeError):
    """Raised when an idempotent retransmission is still executing."""

    def __init__(self, run: ConversationRun) -> None:
        super().__init__("This request is already being processed.")
        self.thread_id = run.thread_id
        self.run_id = run.run_id


class PersistedRunFailedError(RuntimeError):
    """Raised when an idempotent retransmission resolves to a failed run."""

    def __init__(self, run: ConversationRun) -> None:
        message = run.error_message or "The research run failed."
        super().__init__(message)
        self.thread_id = run.thread_id
        self.run_id = run.run_id
        self.error_code = run.error_code or "research_failed"


class ThreadResearchService:
    """Coordinate durable admission, graph execution, and finalization."""

    def __init__(
        self,
        repository: ConversationRepository,
        agent: ThreadResearchAgent,
    ) -> None:
        self._repository = repository
        self._agent = agent
        self._startup_recovery_completed = False

    async def research(
        self,
        message: str,
        *,
        thread_id: UUID | None,
        request_key: UUID | None,
    ) -> ThreadResearchResult:
        """Execute or idempotently replay one durable research turn."""
        with observe_span(
            "mini_alpha.research_run",
            metadata={"mode": "threaded", "streaming": False},
        ) as span:
            try:
                result = await self._research(
                    message,
                    thread_id=thread_id,
                    request_key=request_key,
                )
            except ResearchExecutionError as error:
                span.mark_error_type(error.code)
                span.set_attributes({"outcome": "error", "failure_code": error.code})
                raise
            span.set_attributes(
                {
                    "outcome": "ok",
                    "replayed": result.replayed,
                    "tool_call_count": len(result.tool_calls),
                    "artifact_count": len(result.artifacts),
                }
            )
            return result

    async def _research(
        self,
        message: str,
        *,
        thread_id: UUID | None,
        request_key: UUID | None,
    ) -> ThreadResearchResult:
        """Execute a durable turn beneath the caller's root trace."""
        admission = await self._repository.admit_run(
            thread_id=thread_id,
            message=message,
            request_key=request_key,
        )
        if admission.replayed:
            return await self._replay(admission.run)

        run = admission.run
        try:
            result = await self._agent.research_thread(
                message,
                thread_id=run.thread_id,
                run_id=run.run_id,
                checkpoint_id=admission.from_checkpoint_id,
            )
        except ResearchExecutionError as error:
            message = self._execution_error_message(error)
            await self._repository.fail_run(
                run.run_id,
                error_code=error.code,
                error_message=message,
            )
            raise

        checkpoint_id = result.checkpoint_id
        if checkpoint_id is None:
            await self._repository.fail_run(
                run.run_id,
                error_code="checkpoint_missing",
                error_message="The research agent did not produce a checkpoint.",
            )
            raise ResearchExecutionError(
                "The research agent completed without a readable checkpoint."
            )

        tool_calls = [self._tool_call_payload(call) for call in result.tool_calls]
        try:
            turn = await self._repository.complete_run(
                run.run_id,
                expected_checkpoint_id=admission.from_checkpoint_id,
                checkpoint_id=checkpoint_id,
                answer=result.answer,
                tool_calls=tool_calls,
                artifacts=result.artifacts,
            )
        except CheckpointConflictError:
            await self._repository.fail_run(
                run.run_id,
                error_code="thread_conflict",
                error_message=(
                    "The research thread changed while the request was running."
                ),
            )
            raise

        return self._completed_result(turn, replayed=False)

    async def prepare_stream(
        self,
        message: str,
        *,
        thread_id: UUID | None,
        request_key: UUID | None,
    ) -> ThreadResearchStream:
        """Admit a run before opening HTTP streaming response headers."""
        admission = await self._repository.admit_run(
            thread_id=thread_id,
            message=message,
            request_key=request_key,
        )
        replay = await self._replay(admission.run) if admission.replayed else None
        return ThreadResearchStream(admission=admission, replay=replay)

    async def stream(
        self,
        stream: ThreadResearchStream,
    ) -> AsyncIterator[RunEvent]:
        """Produce one ordered SSE-ready run while preserving commit ordering."""
        with observe_span(
            "mini_alpha.research_run",
            metadata={
                "mode": "threaded",
                "streaming": True,
                "replayed": stream.replay is not None,
            },
        ) as span:
            async for event in self._stream(stream):
                if event.event == "run_end":
                    status = str(event.data.get("status", "unknown"))
                    failure_code = event.data.get("error_code")
                    if status == "error":
                        span.mark_error_type(str(failure_code or "research_failed"))
                    span.set_attributes(
                        {
                            "outcome": status,
                            "failure_code": failure_code,
                        }
                    )
                yield event

    async def _stream(
        self,
        stream: ThreadResearchStream,
    ) -> AsyncIterator[RunEvent]:
        """Produce SSE-ready events beneath the caller's root trace."""
        run = stream.admission.run
        producer = RunEventProducer(run_id=run.run_id, thread_id=run.thread_id)
        yield producer.emit(
            "metadata",
            {
                "turn_index": run.turn_index,
                "replayed": stream.replay is not None,
            },
        )

        if stream.replay is not None:
            for index, call in enumerate(stream.replay.tool_calls):
                yield producer.emit(
                    "tool_call",
                    {
                        "tool_call_id": f"replay-tool-{index}",
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
            for artifact in stream.replay.artifacts:
                yield producer.emit("artifact", artifact)
            if stream.replay.answer:
                yield producer.emit(
                    "message_chunk",
                    {"delta": stream.replay.answer},
                )
            yield producer.emit("run_end", {"status": "completed"})
            return

        result: ResearchResult | None = None
        try:
            async for item in self._agent.stream_thread(
                run.message,
                thread_id=run.thread_id,
                run_id=run.run_id,
                checkpoint_id=stream.admission.from_checkpoint_id,
            ):
                if isinstance(item, AgentStreamEvent):
                    yield producer.emit(item.event, item.data)
                elif isinstance(item, AgentStreamComplete):
                    result = item.result

            if result is None or result.checkpoint_id is None:
                raise ResearchExecutionError(
                    "The research agent completed without a readable checkpoint."
                )

            turn = await self._repository.complete_run(
                run.run_id,
                expected_checkpoint_id=stream.admission.from_checkpoint_id,
                checkpoint_id=result.checkpoint_id,
                answer=result.answer,
                tool_calls=[
                    self._tool_call_payload(call) for call in result.tool_calls
                ],
                artifacts=result.artifacts,
            )
        except CheckpointConflictError:
            await self._repository.fail_run(
                run.run_id,
                error_code="thread_conflict",
                error_message=(
                    "The research thread changed while the request was running."
                ),
            )
            yield producer.emit(
                "error",
                {
                    "code": "thread_conflict",
                    "message": "The research thread changed during execution.",
                },
            )
            yield producer.emit(
                "run_end",
                {"status": "error", "error_code": "thread_conflict"},
            )
            return
        except ResearchExecutionError as error:
            message = self._execution_error_message(error)
            await self._repository.fail_run(
                run.run_id,
                error_code=error.code,
                error_message=message,
            )
            yield producer.emit(
                "error",
                {
                    "code": error.code,
                    "message": message,
                },
            )
            yield producer.emit(
                "run_end",
                {"status": "error", "error_code": error.code},
            )
            return

        completed = self._completed_result(turn, replayed=False)
        yield producer.emit(
            "run_end",
            {"status": "completed", "turn_index": completed.turn_index},
        )

    async def get_thread(self, thread_id: UUID) -> ConversationThread:
        """Return one durable thread or raise a controlled not-found error."""
        thread = await self._repository.get_thread(thread_id)
        if thread is None:
            raise ThreadNotFoundError("The research thread was not found.")
        return thread

    async def list_threads(self, *, limit: int, offset: int) -> ThreadPage:
        """Return a bounded page of durable threads."""
        return await self._repository.list_threads(limit=limit, offset=offset)

    async def list_turns(self, thread_id: UUID) -> tuple[ConversationTurn, ...]:
        """Return the durable transcript for one thread."""
        return await self._repository.list_turns(thread_id)

    async def cancel_run(
        self,
        run_id: UUID,
        *,
        partial_answer: str = "",
        tool_calls: tuple[dict[str, object], ...] = (),
        artifacts: tuple[dict[str, object], ...] = (),
    ) -> ConversationRun:
        """Persist an explicit cancellation for an active run."""
        return await self._repository.cancel_run(
            run_id,
            partial_answer=partial_answer,
            tool_calls=tool_calls,
            artifacts=artifacts,
        )

    async def recover_abandoned_runs(self) -> int:
        """Fail work left active before this process acquired ownership."""
        if self._startup_recovery_completed:
            return 0
        recovered = await self._repository.recover_abandoned_runs()
        self._startup_recovery_completed = True
        return recovered

    async def fail_run(
        self,
        run_id: UUID,
        *,
        error_code: str,
        error_message: str,
    ) -> ConversationRun:
        """Persist an infrastructure failure for an active run."""
        return await self._repository.fail_run(
            run_id,
            error_code=error_code,
            error_message=error_message,
        )

    async def get_turn(self, run_id: UUID) -> ConversationTurn:
        """Return one durable run or raise a controlled not-found error."""
        turn = await self._repository.get_turn(run_id)
        if turn is None:
            raise RunNotFoundError("The research run was not found.")
        return turn

    async def _replay(self, run: ConversationRun) -> ThreadResearchResult:
        """Return or reject an already-admitted request without re-execution."""
        if run.status == "in_progress":
            raise ExistingRunInProgressError(run)
        if run.status in {"error", "cancelled"}:
            raise PersistedRunFailedError(run)
        turn = await self._repository.get_turn(run.run_id)
        if turn is None:
            raise RunNotFoundError("The research run was not found.")
        return self._completed_result(turn, replayed=True)

    def _completed_result(
        self,
        turn: ConversationTurn,
        *,
        replayed: bool,
    ) -> ThreadResearchResult:
        return ThreadResearchResult(
            thread_id=turn.run.thread_id,
            run_id=turn.run.run_id,
            turn_index=turn.run.turn_index,
            answer=turn.run.answer or "",
            tool_calls=self._stored_tool_calls(turn.run),
            artifacts=tuple(
                self._artifact_envelope(artifact) for artifact in turn.artifacts
            ),
            replayed=replayed,
        )

    @staticmethod
    def _execution_error_message(error: ResearchExecutionError) -> str:
        """Return a stable public message without leaking provider details."""
        if error.code == "model_timeout":
            return "The research model timed out."
        if error.code == "tool_timeout":
            return "A research tool timed out."
        return "The research agent could not complete the request."

    @staticmethod
    def _stored_tool_calls(
        run: ConversationRun,
    ) -> tuple[ExecutedToolCall, ...]:
        calls: list[ExecutedToolCall] = []
        for stored in run.tool_calls:
            name = stored.get("name")
            arguments = stored.get("arguments")
            if not isinstance(name, str) or not isinstance(arguments, dict):
                continue
            calls.append(
                ExecutedToolCall(
                    name=name,
                    arguments=cast(dict[str, object], arguments),
                    status=(
                        cast(Literal["ok", "error"], stored.get("status"))
                        if stored.get("status") in {"ok", "error"}
                        else None
                    ),
                    summary=(
                        cast(str, stored.get("summary"))
                        if isinstance(stored.get("summary"), str)
                        else None
                    ),
                )
            )
        return tuple(calls)

    @staticmethod
    def _tool_call_payload(call: ExecutedToolCall) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": call.name,
            "arguments": call.arguments,
        }
        if call.status is not None:
            payload["status"] = call.status
        if call.summary is not None:
            payload["summary"] = call.summary
        return payload

    @staticmethod
    def _artifact_envelope(artifact: StoredArtifact) -> dict[str, object]:
        envelope: dict[str, object] = {
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "status": artifact.status,
        }
        if artifact.data is not None:
            envelope["data"] = artifact.data
        if artifact.error is not None:
            envelope["error"] = artifact.error
        return envelope
