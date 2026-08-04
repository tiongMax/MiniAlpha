# Phase 4–5 API guide

MiniAlpha Phase 5 adds synchronous, PostgreSQL-backed conversations while
retaining Phase 3's stateless research route. Generated OpenAPI documentation
is available at `/docs`, and the machine-readable schema is at
`/openapi.json`.

## Start the service

Set `GEMINI_API_KEY`, `GEMINI_MODEL`, and `DATABASE_URL` in `.env`, then run:

```powershell
docker compose up -d postgres
uv run python -m scripts.setup_database
uv run python -m scripts.run_api --reload
```

Database setup is required for a new database and for new migrations, not for
every API restart.

## System endpoints

### `GET /health`

Reports process liveness without contacting Gemini, Yahoo Finance, or
PostgreSQL:

```json
{
  "status": "ok",
  "service": "mini-alpha",
  "phase": 5
}
```

### `GET /ready`

Checks that the research services were composed and the persistence runtime
can reach its required tables:

```json
{
  "status": "ready",
  "service": "mini-alpha",
  "phase": 5,
  "persistence": "ready"
}
```

It returns `503` when model configuration or durable persistence is
unavailable.

## Create a durable thread

### `POST /api/v1/threads/messages`

Creates a thread, admits its first run, executes the graph, and returns only
after the run is terminal.

Request:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Analyze Apple."
    }
  ],
  "request_key": "07f52e50-c11d-4ef8-b639-cc18fa344412"
}
```

The request accepts exactly one user message. Content is trimmed and must be
between 1 and 10,000 characters. Unknown fields and other roles are rejected.
`request_key` is optional, but retry-safe clients should always send a
client-generated UUID.

Successful response:

```json
{
  "thread_id": "2ab0b79c-4e88-46cf-a9aa-b5e5ca29bc9b",
  "run_id": "84eb50ad-ff9c-432a-84b2-d352912e5804",
  "turn_index": 1,
  "status": "completed",
  "answer": "Apple is a large technology company...",
  "tool_calls": [
    {
      "name": "get_company_overview",
      "arguments": {
        "symbol": "AAPL"
      }
    }
  ],
  "artifacts": [
    {
      "artifact_type": "company_overview",
      "schema_version": 1,
      "status": "ok",
      "data": {
        "symbol": "AAPL"
      }
    }
  ],
  "replayed": false
}
```

The response includes a `Content-Location` header pointing to the thread
transcript.

## Continue a durable thread

### `POST /api/v1/threads/{thread_id}/messages`

Executes one new user message from the thread's last committed LangGraph
checkpoint:

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Now compare it with Microsoft."
    }
  ],
  "request_key": "9e00cc5f-697f-4fcc-bd2b-6aa3310d7337"
}
```

The client sends only the new message. The server does not rebuild graph
history from transcript rows; it resumes from the published checkpoint.

## Idempotent retry behavior

`request_key` identifies one logical request across retransmissions:

- Retrying the same thread and message after completion returns the stored
  answer, tool calls, and artifacts with `replayed: true`.
- Retrying while the original run remains active returns `409
  run_in_progress`.
- Reusing the key for different content or a different thread returns `409
  request_key_conflict`.
- Retrying a terminal failed run returns its stored controlled failure without
  running the graph again.

The key prevents duplicate model and provider work only when the client reuses
it. Omitting the key treats every submission as a new logical request.

## Read durable conversations

### `GET /api/v1/threads`

Lists threads by most recent activity:

```text
GET /api/v1/threads?limit=20&offset=0
```

`limit` must be from 1 through 100 and `offset` must be non-negative.

### `GET /api/v1/threads/{thread_id}`

Returns thread metadata:

```json
{
  "thread_id": "2ab0b79c-4e88-46cf-a9aa-b5e5ca29bc9b",
  "status": "completed",
  "title": null,
  "created_at": "2026-08-04T10:00:00Z",
  "updated_at": "2026-08-04T10:01:00Z"
}
```

### `GET /api/v1/threads/{thread_id}/messages`

Returns ordered durable turns. Each turn contains its run identity, attempt,
lifecycle status, submitted message, final answer or controlled error, tool
calls, artifacts, and timestamps.

## Stateless route

### `POST /api/v1/research`

The Phase 3 endpoint remains unchanged:

```json
{
  "message": "Analyze Apple."
}
```

It starts with fresh graph state and does not create conversation or
checkpoint records.

## Error envelope

Controlled failures use:

```json
{
  "error": {
    "code": "thread_not_found",
    "message": "The research thread was not found."
  }
}
```

| Status | Code | Meaning |
|---|---|---|
| `404` | `thread_not_found` | The requested thread does not exist |
| `404` | `run_not_found` | A stored run could not be resolved |
| `409` | `request_key_conflict` | An idempotency key identifies another request |
| `409` | `run_in_progress` | The thread already has an active run |
| `409` | `thread_conflict` | The published checkpoint changed during execution |
| `409` | `run_conflict` | A terminal run rejected another lifecycle transition |
| `422` | FastAPI validation detail | Input does not match the strict contract |
| `502` | `research_failed` or stored run code | Model or graph execution failed |
| `503` | `research_unavailable` | Stateless research composition is unavailable |
| `503` | `persistence_unavailable` | PostgreSQL/thread composition is unavailable |
| `500` | `internal_error` | An unexpected server failure occurred |

Expected ticker and provider failures still return completed agent results,
usually HTTP `200`, with `status: "error"` artifacts. They are evidence-level
limitations rather than infrastructure failures.

## Request correlation

Every response includes a server-generated `X-Request-ID`. Logs contain the
ID, method, route, status, and duration, but not request content or complete
artifacts. `X-Request-ID` is for tracing; `request_key` is the client-controlled
idempotency identity.

## Live PostgreSQL verification

The standard test suite uses deterministic in-memory adapters and skips live
PostgreSQL tests. With the Compose database healthy, enable the integration
gate using the environment variable recognized in the integration tests, then
run:

```powershell
$env:TEST_DATABASE_URL = `
  "postgresql://minialpha:minialpha@localhost:5433/minialpha"
uv run pytest `
  tests/test_persistence_runtime.py `
  tests/test_postgres_repository.py `
  tests/test_postgres_checkpointer.py `
  tests/test_postgres_api.py
```

The end-to-end PostgreSQL test verifies that a second HTTP turn remembers the
first without the client resending conversation history.

## Current limits

Phase 5 is synchronous and single-process in behavior. It does not yet add:

- Authentication, authorization, users, or workspaces.
- Background execution, cancellation, steering, or recovery of abandoned
  `in_progress` runs.
- Redis, SSE, WebSockets, reconnectable event replay, or streaming tokens.
- Multi-worker ownership or distributed run coordination.
- Thread deletion, branching, editing, or checkpoint garbage collection.
- Rate limiting, quotas, or public-internet deployment hardening.
- PTC, MCP, sandboxing, long-term memory, or subagents.

Bind the development server to localhost unless those controls are added.
