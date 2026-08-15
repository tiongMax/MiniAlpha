"""Run-scoped event transports for replayable live delivery."""

import asyncio
import json
import logging
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol, cast
from uuid import UUID

from app.events.models import EventName, RunEvent

logger = logging.getLogger(__name__)


class RunEventStore(Protocol):
    """Transport behavior required by detached execution and SSE readers."""

    async def publish(self, event: RunEvent) -> None:
        """Append one event to its run's ordered stream."""
        ...

    def events(
        self,
        run_id: UUID,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        """Replay and follow events with identities greater than the cursor."""
        ...

    async def exists(self, run_id: UUID) -> bool:
        """Return whether an unexpired stream exists for the run."""
        ...

    async def latest(self, run_id: UUID) -> RunEvent | None:
        """Return the newest retained event, if any."""
        ...

    async def is_ready(self) -> bool:
        """Return whether the transport can serve commands."""
        ...

    async def close(self) -> None:
        """Release resources owned by the transport."""
        ...


@dataclass(slots=True)
class _MemoryStream:
    events: list[RunEvent] = field(default_factory=list)
    terminal: bool = False
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)


class InMemoryRunEventStore:
    """Deterministic process-local transport used by unit tests."""

    def __init__(
        self,
        *,
        max_streams: int | None = None,
        max_events_per_stream: int | None = None,
    ) -> None:
        if max_streams is not None and max_streams < 1:
            raise ValueError("max_streams must be positive")
        if max_events_per_stream is not None and max_events_per_stream < 1:
            raise ValueError("max_events_per_stream must be positive")
        self._max_streams = max_streams
        self._max_events_per_stream = max_events_per_stream
        self._streams: OrderedDict[UUID, _MemoryStream] = OrderedDict()

    def _stream(self, run_id: UUID) -> _MemoryStream:
        stream = self._streams.get(run_id)
        if stream is not None:
            self._streams.move_to_end(run_id)
            return stream
        if self._max_streams is not None and len(self._streams) >= self._max_streams:
            terminal_run_id = next(
                (
                    stored_run_id
                    for stored_run_id, stored in self._streams.items()
                    if stored.terminal
                ),
                next(iter(self._streams)),
            )
            del self._streams[terminal_run_id]
        stream = _MemoryStream()
        self._streams[run_id] = stream
        return stream

    async def publish(self, event: RunEvent) -> None:
        stream = self._stream(event.run_id)
        async with stream.changed:
            if stream.terminal:
                return
            stream.events.append(event)
            if (
                self._max_events_per_stream is not None
                and len(stream.events) > self._max_events_per_stream
            ):
                del stream.events[: -self._max_events_per_stream]
            stream.terminal = event.event == "run_end"
            stream.changed.notify_all()

    async def events(
        self,
        run_id: UUID,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        stream = self._stream(run_id)
        cursor = after_event_id
        while True:
            async with stream.changed:
                await stream.changed.wait_for(
                    lambda cursor=cursor: (
                        any(event.event_id > cursor for event in stream.events)
                        or stream.terminal
                    )
                )
                pending = tuple(
                    event for event in stream.events if event.event_id > cursor
                )
                terminal = stream.terminal
            for event in pending:
                cursor = event.event_id
                yield event
            if terminal:
                return

    async def exists(self, run_id: UUID) -> bool:
        return run_id in self._streams

    async def latest(self, run_id: UUID) -> RunEvent | None:
        stream = self._streams.get(run_id)
        if stream is not None:
            self._streams.move_to_end(run_id)
        return stream.events[-1] if stream and stream.events else None

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class EventTransportDiagnostic:
    """Bounded, secret-free evidence that primary event delivery degraded."""

    operation: Literal["publish"]
    error_type: str
    timestamp: datetime
    run_id: UUID
    event_id: int


class ResilientRunEventStore:
    """Fail-open publisher backed by a bounded process-local replay buffer.

    The primary transport remains the cross-process source of truth. Every event
    is also recorded locally before primary publication, so a Redis outage cannot
    interrupt graph execution or alter the durable run status. Attachments owned
    by this process read the local copy; attachments after a restart use Redis.
    """

    def __init__(
        self,
        primary: RunEventStore,
        *,
        max_buffered_runs: int = 256,
        max_buffered_events_per_run: int = 256,
        max_diagnostics: int = 100,
        publish_timeout_seconds: float = 1.0,
    ) -> None:
        if max_diagnostics < 1:
            raise ValueError("max_diagnostics must be positive")
        if publish_timeout_seconds <= 0:
            raise ValueError("publish_timeout_seconds must be positive")
        self._primary = primary
        self._fallback = InMemoryRunEventStore(
            max_streams=max_buffered_runs,
            max_events_per_stream=max_buffered_events_per_run,
        )
        self._diagnostics: deque[EventTransportDiagnostic] = deque(
            maxlen=max_diagnostics
        )
        self._max_degraded_runs = max_buffered_runs
        self._degraded_runs: OrderedDict[UUID, None] = OrderedDict()
        self._publish_timeout_seconds = publish_timeout_seconds

    @property
    def diagnostics(self) -> tuple[EventTransportDiagnostic, ...]:
        """Return recent delivery failures without exception text or payloads."""
        return tuple(self._diagnostics)

    async def publish(self, event: RunEvent) -> None:
        await self._fallback.publish(event)
        try:
            await asyncio.wait_for(
                self._primary.publish(event),
                timeout=self._publish_timeout_seconds,
            )
        except Exception as error:
            first_failure = self._mark_degraded(event.run_id)
            diagnostic = EventTransportDiagnostic(
                operation="publish",
                error_type=type(error).__name__,
                timestamp=datetime.now(UTC),
                run_id=event.run_id,
                event_id=event.event_id,
            )
            self._diagnostics.append(diagnostic)
            if first_failure:
                logger.warning(
                    "Run event delivery degraded: operation=publish run_id=%s "
                    "event_id=%d error_type=%s recovery=process_local_buffer",
                    event.run_id,
                    event.event_id,
                    diagnostic.error_type,
                )

    def _mark_degraded(self, run_id: UUID) -> bool:
        first_failure = run_id not in self._degraded_runs
        self._degraded_runs[run_id] = None
        self._degraded_runs.move_to_end(run_id)
        while len(self._degraded_runs) > self._max_degraded_runs:
            self._degraded_runs.popitem(last=False)
        return first_failure

    async def events(
        self,
        run_id: UUID,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        if run_id in self._degraded_runs:
            async for event in self._fallback.events(
                run_id,
                after_event_id=after_event_id,
            ):
                yield event
            return
        async for event in self._primary.events(
            run_id,
            after_event_id=after_event_id,
        ):
            yield event

    async def exists(self, run_id: UUID) -> bool:
        if run_id in self._degraded_runs:
            return await self._fallback.exists(run_id)
        return await self._primary.exists(run_id) or await self._fallback.exists(run_id)

    async def latest(self, run_id: UUID) -> RunEvent | None:
        if run_id in self._degraded_runs:
            return await self._fallback.latest(run_id)
        primary = await self._primary.latest(run_id)
        if primary is not None:
            return primary
        return await self._fallback.latest(run_id)

    async def is_ready(self) -> bool:
        return await self._primary.is_ready()

    async def close(self) -> None:
        await self._fallback.close()
        await self._primary.close()


class RedisRunEventStore:
    """Redis Stream transport with run-scoped retention and blocking reads."""

    def __init__(
        self,
        client: object,
        *,
        retention_seconds: int,
        read_block_milliseconds: int = 1_000,
        key_prefix: str = "mini-alpha:runs",
    ) -> None:
        self._client = client
        self._retention_seconds = retention_seconds
        self._read_block_milliseconds = read_block_milliseconds
        self._key_prefix = key_prefix.rstrip(":")

    @classmethod
    async def open(
        cls,
        redis_url: str,
        *,
        retention_seconds: int,
    ) -> "RedisRunEventStore":
        """Connect to Redis and verify it before accepting runs."""
        from redis.asyncio import Redis

        client = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        store = cls(client, retention_seconds=retention_seconds)
        try:
            await client.ping()
            return store
        except Exception:
            await client.aclose()
            raise

    def _key(self, run_id: UUID) -> str:
        return f"{self._key_prefix}:{run_id}:events"

    async def publish(self, event: RunEvent) -> None:
        payload = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        pipeline = self._client.pipeline(transaction=True)
        pipeline.xadd(
            self._key(event.run_id),
            {"event": payload},
            id=f"{event.event_id}-0",
        )
        pipeline.expire(self._key(event.run_id), self._retention_seconds)
        await pipeline.execute()

    async def events(
        self,
        run_id: UUID,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        key = self._key(run_id)
        cursor = f"{after_event_id}-0"
        latest = await self._client.xrevrange(key, count=1)
        if latest:
            _latest_id, fields = latest[0]
            latest_event = self._decode(fields["event"])
            if (
                latest_event.event == "run_end"
                and latest_event.event_id <= after_event_id
            ):
                return
        while True:
            response = await self._client.xread(
                streams={key: cursor},
                count=100,
                block=self._read_block_milliseconds,
            )
            if not response:
                continue
            for _stream_name, entries in response:
                for redis_id, fields in entries:
                    cursor = redis_id
                    event = self._decode(fields["event"])
                    yield event
                    if event.event == "run_end":
                        return

    async def exists(self, run_id: UUID) -> bool:
        return bool(await self._client.exists(self._key(run_id)))

    async def latest(self, run_id: UUID) -> RunEvent | None:
        entries = await self._client.xrevrange(self._key(run_id), count=1)
        if not entries:
            return None
        _redis_id, fields = entries[0]
        return self._decode(fields["event"])

    async def is_ready(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _decode(payload: str) -> RunEvent:
        raw = cast(dict[str, object], json.loads(payload))
        return RunEvent(
            event_id=int(cast(int | str, raw["event_id"])),
            event=cast(EventName, raw["event"]),
            run_id=UUID(cast(str, raw["run_id"])),
            thread_id=UUID(cast(str, raw["thread_id"])),
            timestamp=datetime.fromisoformat(cast(str, raw["timestamp"])),
            data=cast(dict[str, object], raw["data"]),
        )
