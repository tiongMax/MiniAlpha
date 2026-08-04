# Phase 4–5 architecture decision log

- Status: Accepted
- Date: 2026-08-04
- Phase 4: Durable application records
- Phase 5: PostgreSQL LangGraph checkpoints
- Baseline: Phase 3 stateless FastAPI delivery

## Context

Phase 3 exposed the explicit LangGraph agent over HTTP, but every call began
with fresh state. A real second turn requires two different kinds of
persistence:

1. Application records that clients can list, replay, and reason about.
2. LangGraph checkpoints that preserve the executable conversation state.

These are related because both use PostgreSQL and participate in one turn
lifecycle, but they do not own the same data. Implementing them together keeps
the first persistent endpoint honest: the transcript is durable and the graph
actually remembers prior turns.

The resulting path is:

```text
POST /api/v1/threads/{thread_id}/messages
  -> validate one new user message
  -> idempotently admit an in_progress run
  -> execute explicit LangGraph from the published checkpoint
  -> persist answer, tool calls, and artifacts
  -> compare-and-swap the published checkpoint head
  -> return the terminal result
```

## Goals

- Add durable threads, queries, runs, and structured artifacts.
- Give every execution attempt a stable `run_id`.
- Make client retransmissions safe through a `request_key`.
- Preserve a strict `in_progress` to terminal lifecycle.
- Resume each thread from its last successfully published graph checkpoint.
- Prevent failed or conflicting runs from advancing the visible thread head.
- Keep persistence and graph execution transport-neutral and testable.
- Follow LangAlpha's core state-ownership boundary without importing its
  distributed streaming stack.

## Non-goals

- Redis, SSE, WebSockets, token streaming, or reconnectable event replay.
- Background jobs, cancellation, abandoned-run recovery, or multi-worker
  execution ownership.
- Users, authentication, authorization, workspaces, or sharing.
- Branching, editing, deleting, or garbage-collecting threads.
- PostgreSQL ORM models or a general unit-of-work abstraction.
- PTC, MCP, sandboxing, long-term memory, or subagents.

## Decisions

### P4-001: Separate application records from graph checkpoints

Application tables own:

- Thread identity, status, ordering, and published checkpoint pointer.
- User queries and turn indices.
- Execution attempts, request keys, lifecycle, answers, and tool calls.
- Ordered, versioned artifacts.

LangGraph's checkpoint tables own serialized graph state and pending writes.
Application routes do not decode checkpoint internals to construct transcripts.
Likewise, LangGraph checkpoint tables do not replace the client-facing run
ledger.

This mirrors LangAlpha's division between conversation history and LangGraph
state while keeping MiniAlpha's schema small.

### P4-002: Use raw async psycopg repositories

The persistence boundary is a typed `ConversationRepository` protocol with
in-memory and PostgreSQL implementations. SQL is explicit and uses psycopg's
async pool.

SQLAlchemy was not added because this phase has four focused tables, important
PostgreSQL constraints, and no rich object relationship or portability
requirement. Alembic remains the schema-migration mechanism. An ORM can be
introduced later if aggregate complexity makes its mapping value exceed the
additional abstraction.

### P4-003: Treat a response row as the durable run ledger

Each admitted execution receives:

- `run_id`
- `thread_id`
- `turn_index`
- `attempt_no`
- optional `request_key`
- `in_progress`, `completed`, or `error` status

Database checks enforce valid terminal payloads. A partial unique index permits
only one active run per thread. State transitions use conditional updates so
two writers cannot both finalize the same run.

The table retains its conversation-response name for alignment with the
learning baseline, but its semantics are explicitly execution-attempt
semantics.

### P4-004: Make admission idempotent

A client-generated UUID `request_key` identifies one logical request. The
repository checks the stored thread and normalized message before replaying:

- Same identity and completed status returns stored output.
- Same identity and active status reports `run_in_progress`.
- Same key with different identity reports `request_key_conflict`.
- Same identity and failed status returns the stored controlled failure.

The database unique index makes the guarantee independent of an individual
FastAPI process.

### P4-005: Persist artifacts as ordered versioned records

Artifacts are not embedded only inside an opaque response JSON document. Each
artifact stores its ordinal, type, schema version, success/error status, and
validated payload. This retains Phase 2's evidence contract and allows the
transcript endpoint to reproduce the exact terminal result.

### P5-001: Compile the explicit graph with AsyncPostgresSaver

Production thread composition shares one application-scoped async connection
pool between the repository and `AsyncPostgresSaver`. The graph remains
explicitly built; adding a checkpointer does not replace it with a prebuilt
agent.

The stateless service is still compiled without a checkpointer, so
`POST /api/v1/research` preserves Phase 3 behavior.

### P5-002: Use thread_id for graph continuity and run_id for attempt identity

LangGraph receives the durable `thread_id` in its configurable namespace.
Every submitted human message carries a run-derived marker, and `run_id` is
included as execution metadata. This keeps conversation continuity separate
from one attempt's extraction boundary.

The service returns only messages produced after the current run marker, so a
later turn does not accidentally serialize earlier tool calls as current-turn
evidence.

### P5-003: Publish checkpoint progress with compare-and-swap

Admission captures the thread's current `latest_checkpoint_id`. After graph
execution, finalization advances that pointer only if it still matches the
captured value.

This is a publication fence:

- A successful run atomically publishes its answer, artifacts, and new head.
- A model or graph failure records an error and leaves the old head published.
- A concurrent head change produces `thread_conflict` instead of silently
  overwriting another branch.

LangGraph may have written intermediate checkpoints before application
finalization. Those rows are not considered committed conversation state
unless the application pointer publishes the resulting head.

### P5-004: Keep execution synchronous in this phase

The HTTP request owns graph execution and awaits the terminal result. This
avoids pretending to provide LangAlpha's background/SSE semantics before
Redis replay, cancellation, abandoned-run recovery, and multi-worker ownership
exist.

Consequently, this phase can reject concurrent turns but does not yet recover
an `in_progress` run left by a terminated process.

### P5-005: Compose persistence once per application lifespan

FastAPI startup opens one async pool, verifies required tables, constructs the
repository and checkpointer, and compiles the checkpointed graph. Shutdown
closes the owned pool.

`GET /health` remains dependency-free liveness. `GET /ready` verifies research
composition and persistence reachability. Startup failure keeps liveness
available while durable routes return a controlled `503`.

### P5-006: Use explicit database initialization

`scripts.setup_database` applies Alembic migrations and invokes LangGraph's
checkpoint setup. It is safe to rerun and is required for new databases or new
migrations, not every application start.

On Windows, both database setup and the supported API launcher select a
Selector event loop because psycopg async does not support the default
Proactor loop. The selection is process-local.

### P5-007: Preserve stable HTTP boundaries

Durable routes accept exactly one new user message rather than client-supplied
history. Responses expose application identities and terminal data without
leaking LangChain messages or checkpoint internals.

Expected Yahoo/ticker failures remain successful agent executions with
structured error artifacts. Model and graph failures return controlled `502`
responses. Persistence unavailability returns `503`; conflicts return `409`;
unexpected failures retain the generic `500` envelope.

## LangAlpha alignment

MiniAlpha now implements these LangAlpha-shaped concepts:

- Separate durable conversation records and LangGraph checkpoints.
- Thread and run identities with per-turn lifecycle records.
- Client request identity for retry deduplication.
- A published checkpoint pointer rather than treating every checkpoint write
  as visible conversation progress.
- PostgreSQL-backed state that survives API restarts.
- Thin HTTP routes around transport-neutral orchestration.

It deliberately does not claim parity with LangAlpha's production execution
system. LangAlpha adds Redis-backed streaming/replay, background workers,
multi-worker fencing, users/workspaces, recovery, and many other controls.
Those belong to later phases rather than being approximated here.

## Verification

Credential-free tests cover:

- Repository lifecycle, idempotency, replay, conflicts, and artifact checks.
- Checkpoint-aware graph configuration and current-run extraction.
- Durable service admission, success, failure, and replay.
- FastAPI create/continue/list/detail/transcript and stable error contracts.
- Application lifespan ownership, liveness, and readiness.
- Generated OpenAPI paths and Phase 5 metadata.
- Windows-compatible database and API event-loop selection.

Optional live PostgreSQL tests cover:

- Migration/runtime readiness.
- Repository constraints and compare-and-swap publication.
- AsyncPostgresSaver round trips.
- Full HTTP-to-PostgreSQL execution where turn two remembers turn one without
  resending history.

## Result

Phase 4 and Phase 5 are implemented together because a durable transcript and
durable executable state form one honest conversation feature. They remain
separate architectural layers, which leaves a clear path to later Redis/SSE
delivery and distributed execution without coupling HTTP contracts to
LangGraph's storage representation.
