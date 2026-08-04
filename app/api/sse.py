"""Server-Sent Events framing for the stable run event protocol."""

import json

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
