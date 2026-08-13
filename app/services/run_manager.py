"""Application-scoped detached execution and replayable event attachment."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.events.models import RunEvent
from app.events.store import (
    EventTransportDiagnostic,
    InMemoryRunEventStore,
    ResilientRunEventStore,
    RunEventStore,
)
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
    """Process-local execution ownership and cancellation state."""

    run: ConversationRun
    events: list[RunEvent] = field(default_factory=list)
    terminal: bool = False
    cancel_requested: bool = False
    execution: asyncio.Task[None] | None = None
    changed: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def record(self, event: RunEvent) -> bool:
        """Record locally-owned evidence and reject events after terminal."""
        async with self.changed:
            if self.terminal:
                return False
            self.events.append(event)
            if event.event == "run_end":
                self.terminal = True
            return True

    async def publish_terminal(
        self, status: str, error_code: str | None = None
    ) -> RunEvent | None:
        async with self.changed:
            if self.terminal:
                return None
            data: dict[str, object] = {"status": status}
            if error_code is not None:
                data["error_code"] = error_code
            self.events.append(
                RunEvent(
                    event_id=len(self.events) + 1,
                    event="run_end",
                    run_id=self.run.run_id,
                    thread_id=self.run.thread_id,
                    timestamp=datetime.now(UTC),
                    data=data,
                )
            )
            self.terminal = True
            return self.events[-1]

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

    def __init__(
        self,
        service: ThreadResearchService,
        *,
        event_store: RunEventStore | None = None,
        shutdown_grace_seconds: float = 10.0,
    ) -> None:
        self._service = service
        primary_event_store = event_store or InMemoryRunEventStore()
        self._event_store = ResilientRunEventStore(primary_event_store)
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._channels: dict[UUID, _RunChannel] = {}
        self._queue: asyncio.Queue[ThreadResearchStream | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._accepting = False

    @property
    def event_transport_diagnostics(self) -> tuple[EventTransportDiagnostic, ...]:
        """Return bounded diagnostics for primary transport publish failures."""
        return self._event_store.diagnostics

    async def start(self) -> None:
        """Start the single process-local execution worker."""
        if self._worker is None:
            await self._service.recover_abandoned_runs()
            self._accepting = True
            self._worker = asyncio.create_task(self._work(), name="mini-alpha-runs")

    async def close(self) -> None:
        """Drain accepted work and stop the process-local worker."""
        if self._worker is None:
            return
        self._accepting = False
        await self._queue.put(None)
        try:
            await asyncio.wait_for(
                asyncio.shield(self._worker),
                timeout=self._shutdown_grace_seconds,
            )
        except TimeoutError:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            await self._interrupt_open_channels()
        self._worker = None

    async def submit(
        self,
        message: str,
        *,
        thread_id: UUID | None,
        request_key: UUID | None,
    ) -> RunSubmission:
        """Durably admit a run, enqueue it, and return without executing it."""
        if not self._accepting:
            raise RuntimeError("The run worker is not accepting new work.")
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

    async def ensure_events_available(self, run_id: UUID) -> None:
        """Validate a run cursor before response headers are committed."""
        turn = await self._service.get_turn(run_id)
        if turn.run.status == "in_progress":
            return
        latest = await self._event_store.latest(run_id)
        if latest is None:
            raise RunNotFoundError("Live events for this run have expired.")
        if latest.event == "run_end" or run_id in self._channels:
            return
        data: dict[str, object] = {"status": turn.run.status}
        if turn.run.error_code is not None:
            data["error_code"] = turn.run.error_code
        await self._publish_event(
            RunEvent(
                event_id=latest.event_id + 1,
                event="run_end",
                run_id=turn.run.run_id,
                thread_id=turn.run.thread_id,
                timestamp=datetime.now(UTC),
                data=data,
            )
        )

    async def events(
        self,
        run_id: UUID,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        """Replay and follow events strictly after the supplied cursor."""
        await self.ensure_events_available(run_id)
        async for event in self._event_store.events(
            run_id,
            after_event_id=after_event_id,
        ):
            yield event

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
        terminal = await channel.publish_terminal("cancelled")
        if terminal is not None:
            await self._publish_event(terminal)
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
                    if asyncio.current_task().cancelling():
                        if not execution.done():
                            execution.cancel()
                        raise
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
                if await channel.record(event):
                    await self._publish_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The service translates expected graph failures. Reaching this path
            # indicates an unexpected worker failure; preserve durable truth.
            if not channel.terminal:
                try:
                    failed = await self._service.fail_run(
                        channel.run.run_id,
                        error_code="worker_failed",
                        error_message="The run worker stopped unexpectedly.",
                    )
                    channel.run = failed
                except RunLifecycleConflictError:
                    pass
                terminal = await channel.publish_terminal("error", "worker_failed")
                if terminal is not None:
                    await self._publish_event(terminal)

    async def _publish_event(self, event: RunEvent) -> None:
        """Deliver best-effort events without changing durable run outcomes."""
        try:
            await self._event_store.publish(event)
        except Exception:
            # ResilientRunEventStore already converts primary failures to
            # diagnostics. This final boundary ensures an unexpected fallback
            # defect still cannot be misclassified as a research worker failure.
            return

    async def _interrupt_open_channels(self) -> None:
        """Durably fail accepted work that outlived the shutdown grace period."""
        for channel in self._channels.values():
            if channel.terminal:
                continue
            try:
                failed = await self._service.fail_run(
                    channel.run.run_id,
                    error_code="process_interrupted",
                    error_message=(
                        "The worker process stopped before completing the run."
                    ),
                )
                channel.run = failed
            except RunLifecycleConflictError:
                continue
            terminal = await channel.publish_terminal("error", "process_interrupted")
            if terminal is not None:
                await self._publish_event(terminal)
