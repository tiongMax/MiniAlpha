"""Server-Sent Events framing for the stable run event protocol."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress

from app.events.models import RunEvent

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def encode_sse(event: RunEvent) -> str:
    """Encode one run event as a valid SSE frame."""
    payload = json.dumps(
        event.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {event.event_id}\nevent: {event.event}\ndata: {payload}\n\n"


async def encode_sse_stream(
    events: AsyncIterator[RunEvent],
    *,
    keepalive_seconds: float,
) -> AsyncIterator[str]:
    """Encode events and emit SSE comments while Redis reads are idle."""
    pending = asyncio.create_task(anext(events))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=keepalive_seconds)
            if not done:
                yield ": keepalive\n\n"
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            yield encode_sse(event)
            pending = asyncio.create_task(anext(events))
    finally:
        if not pending.done():
            pending.cancel()
        with suppress(asyncio.CancelledError, StopAsyncIteration):
            await pending
        await events.aclose()
