# Phase 7 detached execution API

Phase 7 separates run execution from the HTTP connection. The application
owns one process-local background worker. PostgreSQL remains lifecycle truth;
the in-memory event buffer is only live transport until Phase 8 adds Redis.

## Flow

1. `POST /api/v1/threads/runs` creates a thread and admits its first run.
2. `POST /api/v1/threads/{thread_id}/runs` admits a continuation.
3. The response is `202 Accepted` with `run_id`, `thread_id`, and `events_url`.
4. `GET /api/v1/runs/{run_id}/events` replays buffered events and follows new
   events until `run_end`.
5. `POST /api/v1/runs/{run_id}/cancel` interrupts execution and commits the
   durable `cancelled` terminal state.

Disconnecting an events request does not cancel the run. A later attachment in
the same API process receives the buffered events from the beginning. Event
buffers do not survive process restart yet; that is the Phase 8 Redis boundary.

## Lifecycle guarantees

- Admission commits the `in_progress` row before returning the `run_id`.
- One worker executes queued runs independently of SSE consumers.
- One thread still has at most one `in_progress` run.
- `completed`, `error`, and `cancelled` are terminal states.
- `run_end` follows the corresponding terminal database commit.
- Reusing a `request_key` attaches to the original in-process run rather than
  starting duplicate model work.

Apply migration `002_phase_7_cancellation` before starting the Phase 7 API:

```powershell
uv run python -m scripts.setup_database
```
