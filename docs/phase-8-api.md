# Phase 8 Redis reconnect

Phase 8 keeps PostgreSQL as run-lifecycle truth and moves live event transport
from process memory to one Redis Stream per `run_id`.

## Reconnect flow

1. Admit work with `POST /api/v1/threads/runs` or
   `POST /api/v1/threads/{thread_id}/runs`.
2. Attach to the returned `events_url`.
3. Record the SSE `id` after reducing each event.
4. If the connection ends before `run_end`, attach again with
   `Last-Event-ID: <id>`.
5. Redis replays entries strictly after that ID, then blocks for new entries.

Application event `1` is stored as Redis entry `1-0`, event `2` as `2-0`, and
so on. This keeps Redis ordering aligned with the stable numeric SSE protocol.
The stream sends `: keepalive` comments while idle. Comments contain no
application event and must not advance the reconnect cursor.

## Retention and truth boundaries

- `RUN_EVENT_RETENTION_SECONDS` controls stream expiration and defaults to
  86,400 seconds.
- Expiration is refreshed whenever an event is appended.
- After expiration, the durable transcript remains available from PostgreSQL,
  but the events endpoint returns `404` for a terminal run.
- `run_end` is appended only after the matching terminal PostgreSQL commit.
- Redis loss affects live delivery, not the authoritative run status.

Production requires `REDIS_URL`. Start local infrastructure with:

```powershell
docker compose up -d postgres redis
```
