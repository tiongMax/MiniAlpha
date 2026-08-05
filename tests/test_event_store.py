"""Phase 8 replay transport and keepalive tests."""

import asyncio
from uuid import uuid4

from app.api.sse import encode_sse_stream
from app.events.models import RunEventProducer
from app.events.store import InMemoryRunEventStore, RedisRunEventStore


class FakePipeline:
    """Minimal redis-py pipeline double for ordered append assertions."""

    def __init__(self, client) -> None:
        self.client = client
        self.commands: list[tuple[str, tuple[object, ...]]] = []

    def xadd(self, key, fields, *, id):
        self.commands.append(("xadd", (key, fields, id)))
        return self

    def expire(self, key, seconds):
        self.commands.append(("expire", (key, seconds)))
        return self

    async def execute(self):
        self.client.executions.append(tuple(self.commands))
        for name, arguments in self.commands:
            if name == "xadd":
                key, fields, redis_id = arguments
                self.client.entries.setdefault(key, []).append((redis_id, fields))
        return [True] * len(self.commands)


class FakeRedis:
    def __init__(self) -> None:
        self.entries: dict[object, list[tuple[object, object]]] = {}
        self.executions: list[tuple[tuple[str, tuple[object, ...]], ...]] = []

    def pipeline(self, *, transaction):
        assert transaction is True
        return FakePipeline(self)

    async def xread(self, *, streams, count, block):
        assert count == 100
        assert block == 1_000
        key, cursor = next(iter(streams.items()))
        cursor_number = int(str(cursor).split("-", 1)[0])
        pending = [
            entry
            for entry in self.entries.get(key, [])
            if int(str(entry[0]).split("-", 1)[0]) > cursor_number
        ]
        return [(key, pending)] if pending else []

    async def xrevrange(self, key, *, count):
        assert count == 1
        entries = self.entries.get(key, [])
        return entries[-1:] if entries else []

    async def exists(self, key):
        return int(key in self.entries)

    async def ping(self):
        return True

    async def aclose(self):
        return None


def test_in_memory_store_replays_strictly_after_cursor() -> None:
    async def exercise():
        store = InMemoryRunEventStore()
        producer = RunEventProducer(run_id=uuid4(), thread_id=uuid4())
        events = [
            producer.emit("metadata", {}),
            producer.emit("message_chunk", {"delta": "answer"}),
            producer.emit("run_end", {"status": "completed"}),
        ]
        for event in events:
            await store.publish(event)
        return [
            event
            async for event in store.events(
                events[0].run_id,
                after_event_id=1,
            )
        ]

    replayed = asyncio.run(exercise())
    assert [event.event_id for event in replayed] == [2, 3]


def test_redis_store_uses_ordered_ids_and_refreshes_expiration() -> None:
    async def exercise():
        client = FakeRedis()
        store = RedisRunEventStore(client, retention_seconds=3_600)
        producer = RunEventProducer(run_id=uuid4(), thread_id=uuid4())
        events = [
            producer.emit("metadata", {}),
            producer.emit("run_end", {"status": "completed"}),
        ]
        for event in events:
            await store.publish(event)
        replayed = [
            event
            async for event in store.events(
                events[0].run_id,
                after_event_id=1,
            )
        ]
        return client, replayed

    client, replayed = asyncio.run(exercise())
    assert [event.event_id for event in replayed] == [2]
    assert [execution[0][1][2] for execution in client.executions] == ["1-0", "2-0"]
    assert all(execution[1][0] == "expire" for execution in client.executions)
    assert all(execution[1][1][1] == 3_600 for execution in client.executions)


def test_redis_store_closes_when_cursor_already_acknowledges_terminal() -> None:
    async def exercise():
        client = FakeRedis()
        store = RedisRunEventStore(client, retention_seconds=3_600)
        producer = RunEventProducer(run_id=uuid4(), thread_id=uuid4())
        terminal = producer.emit("run_end", {"status": "completed"})
        await store.publish(terminal)
        return [
            event
            async for event in store.events(
                terminal.run_id,
                after_event_id=terminal.event_id,
            )
        ]

    assert asyncio.run(exercise()) == []


def test_sse_stream_sends_keepalive_comments_while_idle() -> None:
    async def exercise():
        release = asyncio.Event()
        producer = RunEventProducer(run_id=uuid4(), thread_id=uuid4())

        async def events():
            await release.wait()
            yield producer.emit("run_end", {"status": "completed"})

        frames = encode_sse_stream(events(), keepalive_seconds=0.001)
        keepalive = await anext(frames)
        release.set()
        terminal = await anext(frames)
        await frames.aclose()
        return keepalive, terminal

    keepalive, terminal = asyncio.run(exercise())
    assert keepalive == ": keepalive\n\n"
    assert "event: run_end" in terminal
