# MiniAlpha

MiniAlpha is a learning project that rebuilds the core research loop behind
LangAlpha with an explicit LangGraph instead of
`langchain.agents.create_agent`.

## Phase 7 + Phase 13 reliability slice

Phase 7 supports independent research requests, durable conversations, a
stable live event stream, detached execution, and explicit cancellation:

```text
HTTP client
  -> FastAPI
  -> DetachedRunManager (one background worker)
       -> ThreadResearchService
       -> conversation repository -> PostgreSQL application tables
       -> ResearchAgentService -> explicit LangGraph
                                  -> PostgreSQL checkpoints
                                  -> company research tool -> Yahoo Finance
                                  -> application event translator -> SSE
```

Phase 4 introduced application-owned records for threads, queries, runs, and
artifacts. Phase 5 added PostgreSQL checkpoints. Phase 6 translates live graph
activity into a small application-owned SSE protocol without exposing raw
LangGraph events.

This follows LangAlpha's important persistence boundary at learning scale:
the application database owns request identity, run lifecycle, transcripts,
and the published checkpoint pointer; LangGraph owns serialized graph state.
MiniAlpha does not yet copy LangAlpha's Redis replay, multi-worker
coordination, authentication, workspaces, sandboxing, MCP, PTC, or subagent
infrastructure.

A small React frontend now consumes the Phase 7 API so the agent can be tested
interactively. It provides a durable thread list, transcript loading, streaming
assistant text, tool progress, and structured artifact inspection. TanStack
React Query caches committed thread/transcript server state; provisional SSE
events remain in a local reducer until the completed transcript is refetched.
The Stop control performs durable server-side cancellation. Redis-backed
reconnect remains intentionally absent until Phase 8.

The original stateless endpoint remains available. Each call to it starts with
fresh graph state.

## Setup

Copy `.env.example` to `.env`, then set:

```dotenv
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
DATABASE_URL=postgresql://minialpha:minialpha@localhost:5433/minialpha
```

Install dependencies:

```powershell
uv sync
```

Start PostgreSQL and initialize the schema:

```powershell
docker compose up -d postgres
uv run python -m scripts.setup_database
```

Run `scripts.setup_database` when creating a new database, after intentionally
removing its Docker volume, or after pulling a new migration. It is safe to
rerun, but it is not required before every server start.

The Compose service maps host port `5433` to PostgreSQL's container port
`5432`, allowing it to run beside a local PostgreSQL installation. Stop it
without deleting data using:

```powershell
docker compose down
```

Use `docker compose down -v` only when intentionally deleting MiniAlpha's
development database volume.

Start the API:

```powershell
uv run python -m scripts.run_api --reload
```

In a second terminal, start the frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The Vite development server proxies API requests
to `http://127.0.0.1:8000`.

The project launcher selects the event loop required by async psycopg on
Windows. It affects only this API process and does not modify laptop-wide
Python or asyncio settings.

Interactive OpenAPI documentation is available at
`http://127.0.0.1:8000/docs`.

Check process liveness and dependency readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

`/health` only proves the HTTP process is alive. `/ready` also verifies model
composition and PostgreSQL persistence.

## Durable research

Create a thread and execute its first turn:

```powershell
$requestKey = [guid]::NewGuid()
$first = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/threads/messages `
  -ContentType application/json `
  -Body (@{
    messages = @(@{ role = "user"; content = "Analyze Apple." })
    request_key = $requestKey
  } | ConvertTo-Json -Depth 4)
```

Continue from the committed checkpoint without resending history:

```powershell
$second = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/threads/$($first.thread_id)/messages" `
  -ContentType application/json `
  -Body (@{
    messages = @(@{
      role = "user"
      content = "Now compare it with Microsoft."
    })
    request_key = [guid]::NewGuid()
  } | ConvertTo-Json -Depth 4)
```

Start a detached run, then attach a streaming HTTP client:

```text
POST /api/v1/threads/runs
POST /api/v1/threads/{thread_id}/runs
GET  /api/v1/runs/{run_id}/events
POST /api/v1/runs/{run_id}/cancel
```

The stream emits `metadata`, `message_chunk`, `tool_call`, `tool_result`,
`artifact`, `error`, and `run_end`. `metadata` is first and `run_end` is last.
A successful or cancelled `run_end` is emitted only after the terminal
PostgreSQL commit. Browser disconnects detach from SSE without cancelling the
background run. Events remain process-local in Phase 7; durable reconnectable
replay is Phase 8 work. The older `/messages/stream` endpoints remain available
as compatibility wrappers around detached execution.

On startup, the worker marks runs left `in_progress` by an earlier process as
`error` with `process_interrupted`. Shutdown drains accepted work for
`WORKER_SHUTDOWN_GRACE_SECONDS`; work still running after that deadline is
interrupted and terminalized with the same error code. Individual model and
tool steps are bounded by `MODEL_TIMEOUT_SECONDS` and `TOOL_TIMEOUT_SECONDS`,
which produce `model_timeout` and `tool_timeout` terminal errors respectively.

Clients should generate one `request_key` UUID per logical request and reuse it
when retrying that same request. A completed retry returns the stored result
with `"replayed": true` instead of running Gemini again.

List threads and read the transcript:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/threads
Invoke-RestMethod `
  "http://127.0.0.1:8000/api/v1/threads/$($first.thread_id)/messages"
```

## Stateless research and CLI

Submit one independent API request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/research `
  -ContentType application/json `
  -Body '{"message":"Analyze Apple."}'
```

Run the interactive stateless CLI:

```powershell
uv run python cli.py
```

Both delivery paths share `ResearchAgentService`; the durable HTTP path adds
the thread orchestration and checkpoint configuration around it.

Exercise Yahoo directly without using Gemini:

```powershell
uv run python -m scripts.smoke_company AAPL MSFT BRK-B
```

Yahoo Finance data may be delayed, incomplete, or unavailable. MiniAlpha
preserves missing values as `None`/`N/A` and does not silently turn them into
zero. Expected provider and ticker failures remain completed agent results
with structured error artifacts.

## Verification

Run the credential-free suite and code-quality gates:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The PostgreSQL integration tests are skipped unless their explicit test
environment variable is enabled. The Phase 4–5 API guide documents the live
database command.

## Code map

```text
app/config.py                    model and database configuration
app/agent/                       explicit graph, state, tools, and routing
app/api/main.py                  FastAPI factory, lifespan, and error mapping
app/api/routes/                  health, readiness, stateless, and thread routes
app/api/schemas.py               strict public HTTP contracts
app/domain/                      normalized company data and expected errors
app/persistence/                 repository contract and memory/Postgres adapters
app/providers/                   provider protocol and Yahoo implementation
app/services/company_research.py provider-neutral financial-data orchestration
app/services/research_agent.py   transport-neutral graph execution
app/services/thread_research.py  durable admission, execution, and finalization
app/services/run_manager.py      detached worker, event attachment, cancellation
migrations/                      application-owned PostgreSQL schema
scripts/setup_database.py        Alembic and LangGraph checkpoint initialization
scripts/run_api.py               psycopg-compatible API launcher
cli.py                           interactive stateless trace runner
```

## Architecture rationale

- [Phase 2 decision log](docs/phase-2-decision-log.md)
- [Phase 3 decision log](docs/phase-3-decision-log.md)
- [Phase 3 API guide](docs/phase-3-api.md)
- [Phase 4–5 decision log](docs/phase-4-5-decision-log.md)
- [Phase 4–5 API guide](docs/phase-4-5-api.md)
- [Frontend architecture and run guide](docs/frontend.md)
- [Phase 7 API and lifecycle guide](docs/phase-7-api.md)
