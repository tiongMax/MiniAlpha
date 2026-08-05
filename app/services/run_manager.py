"""Application-scoped detached execution and ephemeral event attachment."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.events.models import RunEvent
from app.persistence.models import ConversationRun
from app.persistence.repository import RunLifecycleConflictError, RunNotFoundError
from app.services.thread_research import (
    ExistingRunInProgressError,
    PersistedRunFailedError,
    ThreadResearchService,
    ThreadResearchStream,
)


@dataclass(frozen=True, slots=True)
class RunSubmission:
    """Identity returned as soon as a detached run has been admitted."""

    run_id: UUID
    thread_id: UUID
    turn_index: int
    status: str
    replayed: bool


@dataclass(slots=True)
class _RunChannel:
    """Process-local event buffer shared by execution and SSE attachments."""

    run: ConversationRun
    events: list[RunEvent] = field(default_factory=list)
    terminal: bool = False
    cancel_requested: bool = False
    execution: asyncio.Task[None] | None = None
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    async def publish(self, event: RunEvent) -> None:
        async with self.changed:
            if self.terminal:
                return
            self.events.append(event)
            if event.event == "run_end":
                self.terminal = True
            self.changed.notify_all()

    async def publish_cancelled(self) -> None:
        async with self.changed:
            if self.terminal:
                return
            self.events.append(
                RunEvent(
                    event_id=len(self.events) + 1,
                    event="run_end",
                    run_id=self.run.run_id,
                    thread_id=self.run.thread_id,
                    timestamp=datetime.now(UTC),
                    data={"status": "cancelled"},
                )
            )
            self.terminal = True
            self.changed.notify_all()

    async def cancellation_snapshot(
        self,
    ) -> tuple[
        str,
        tuple[dict[str, object], ...],
        tuple[dict[str, object], ...],
    ]:
        """Collect model text and structured evidence emitted before Stop."""
        async with self.changed:
            answer_parts: list[str] = []
            tool_calls: list[dict[str, object]] = []
            artifacts: list[dict[str, object]] = []
            for event in self.events:
                if event.event == "message_chunk":
                    delta = event.data.get("delta")
                    if isinstance(delta, str):
                        answer_parts.append(delta)
                elif event.event == "tool_call":
                    name = event.data.get("name")
                    arguments = event.data.get("arguments")
                    if isinstance(name, str) and isinstance(arguments, dict):
                        tool_calls.append({"name": name, "arguments": dict(arguments)})
                elif event.event == "artifact":
                    artifacts.append(dict(event.data))
            return "".join(answer_parts), tuple(tool_calls), tuple(artifacts)


class DetachedRunManager:
    """Own one background worker independently of HTTP connections."""

    def __init__(self, service: ThreadResearchService) -> None:
        self._service = service
        self._channels: dict[UUID, _RunChannel] = {}
        self._queue: asyncio.Queue[ThreadResearchStream | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the single process-local execution worker."""
        if self._worker is None:
            self._worker = asyncio.create_task(self._work(), name="mini-alpha-runs")

    async def close(self) -> None:
        """Drain accepted work and stop the process-local worker."""
        if self._worker is None:
            return
        await self._queue.put(None)
        await self._worker
        self._worker = None

    async def submit(
        self,
        message: str,
        *,
        thread_id: UUID | None,
        request_key: UUID | None,
    ) -> RunSubmission:
        """Durably admit a run, enqueue it, and return without executing it."""
        try:
            prepared = await self._service.prepare_stream(
                message,
                thread_id=thread_id,
                request_key=request_key,
            )
        except ExistingRunInProgressError as error:
            channel = self._channels.get(error.run_id)
            if channel is None:
                raise
            return RunSubmission(
                run_id=error.run_id,
                thread_id=error.thread_id,
                turn_index=channel.run.turn_index,
                status=channel.run.status,
                replayed=True,
            )
        except PersistedRunFailedError as error:
            channel = self._channels.get(error.run_id)
            if channel is None:
                raise
            return RunSubmission(
                run_id=error.run_id,
                thread_id=error.thread_id,
                turn_index=channel.run.turn_index,
                status=channel.run.status,
                replayed=True,
            )

        run = prepared.admission.run
        channel = self._channels.get(run.run_id)
        if channel is None:
            channel = _RunChannel(run=run)
            self._channels[run.run_id] = channel
            await self._queue.put(prepared)
        return RunSubmission(
            run_id=run.run_id,
            thread_id=run.thread_id,
            turn_index=run.turn_index,
            status=run.status,
            replayed=prepared.admission.replayed,
        )

    async def events(self, run_id: UUID) -> AsyncIterator[RunEvent]:
        """Replay buffered events, then follow the run until terminal."""
        channel = self._channels.get(run_id)
        if channel is None:
            await self._service.get_turn(run_id)
            raise RunNotFoundError(
                "Live events for this run are no longer available in this process."
            )

        index = 0
        while True:
            async with channel.changed:
                await channel.changed.wait_for(
                    lambda index=index: index < len(channel.events) or channel.terminal
                )
                pending = tuple(channel.events[index:])
                index = len(channel.events)
                terminal = channel.terminal
            for event in pending:
                yield event
            if terminal:
                return

    async def cancel(self, run_id: UUID) -> ConversationRun:
        """Persist cancellation and interrupt graph execution if it is active."""
        channel = self._channels.get(run_id)
        if channel is None:
            turn = await self._service.get_turn(run_id)
            if turn.run.status != "in_progress":
                raise RunLifecycleConflictError("The research run is already terminal.")
            raise RunNotFoundError("The active run is not owned by this process.")

        channel.cancel_requested = True
        execution = channel.execution
        if execution is not None and not execution.done():
            execution.cancel()
            try:
                await execution
            except asyncio.CancelledError:
                pass
        partial_answer, tool_calls, artifacts = await channel.cancellation_snapshot()
        cancelled = await self._service.cancel_run(
            run_id,
            partial_answer=partial_answer,
            tool_calls=tool_calls,
            artifacts=artifacts,
        )
        channel.run = cancelled
        await channel.publish_cancelled()
        return cancelled

    async def _work(self) -> None:
        while True:
            prepared = await self._queue.get()
            try:
                if prepared is None:
                    return
                channel = self._channels[prepared.admission.run.run_id]
                if channel.cancel_requested:
                    continue
                execution = asyncio.create_task(self._execute(prepared, channel))
                channel.execution = execution
                try:
                    await execution
                except asyncio.CancelledError:
                    pass
                finally:
                    channel.execution = None
            finally:
                self._queue.task_done()

    async def _execute(
        self,
        prepared: ThreadResearchStream,
        channel: _RunChannel,
    ) -> None:
        try:
            async for event in self._service.stream(prepared):
                await channel.publish(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The service translates expected graph failures. Reaching this path
            # indicates an unexpected worker failure; preserve durable truth.
            if not channel.terminal:
                try:
                    await self._service.cancel_run(channel.run.run_id)
                except RunLifecycleConflictError:
                    pass
                await channel.publish_cancelled()
