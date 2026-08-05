"""Run-scoped event transports for replayable live delivery."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, cast
from uuid import UUID

from app.events.models import EventName, RunEvent


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

    def __init__(self) -> None:
        self._streams: dict[UUID, _MemoryStream] = {}

    async def publish(self, event: RunEvent) -> None:
        stream = self._streams.setdefault(event.run_id, _MemoryStream())
        async with stream.changed:
            if stream.terminal:
                return
            stream.events.append(event)
            stream.terminal = event.event == "run_end"
            stream.changed.notify_all()

    async def events(
        self,
        run_id: UUID,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        stream = self._streams.setdefault(run_id, _MemoryStream())
        index = 0
        while (
            index < len(stream.events)
            and stream.events[index].event_id <= after_event_id
        ):
            index += 1
        while True:
            async with stream.changed:
                await stream.changed.wait_for(
                    lambda index=index: index < len(stream.events) or stream.terminal
                )
                pending = tuple(stream.events[index:])
                index = len(stream.events)
                terminal = stream.terminal
            for event in pending:
                yield event
            if terminal:
                return

    async def exists(self, run_id: UUID) -> bool:
        return run_id in self._streams

    async def latest(self, run_id: UUID) -> RunEvent | None:
        stream = self._streams.get(run_id)
        return stream.events[-1] if stream and stream.events else None

    async def is_ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None


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
