"""Transport-neutral orchestration for durable research turns."""

from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from app.persistence.models import (
    ConversationRun,
    ConversationThread,
    ConversationTurn,
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

    async def research(
        self,
        message: str,
        *,
        thread_id: UUID | None,
        request_key: UUID | None,
    ) -> ThreadResearchResult:
        """Execute or idempotently replay one durable research turn."""
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
        except ResearchExecutionError:
            await self._repository.fail_run(
                run.run_id,
                error_code="research_failed",
                error_message="The research agent could not complete the request.",
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

        tool_calls = [
            {
                "name": call.name,
                "arguments": call.arguments,
            }
            for call in result.tool_calls
        ]
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

    async def _replay(self, run: ConversationRun) -> ThreadResearchResult:
        """Return or reject an already-admitted request without re-execution."""
        if run.status == "in_progress":
            raise ExistingRunInProgressError(run)
        if run.status == "error":
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
                )
            )
        return tuple(calls)

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
