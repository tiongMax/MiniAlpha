# Phase 3 architecture decision log

- Status: Accepted
- Date: 2026-08-04
- Phase: Basic FastAPI server
- Baseline: Phase 2 real company-overview research

## Context

Phase 2 proved the provider-neutral path from Gemini through the explicit
LangGraph tool loop to Yahoo Finance. Phase 3 needs to make that capability
available over HTTP without importing LangAlpha's production persistence,
streaming, workspace, and distributed-execution infrastructure.

The resulting request path is:

```text
POST /api/v1/research
  -> FastAPI route
  -> ResearchAgentService
  -> explicit LangGraph
  -> company overview tool
  -> CompanyResearchService
  -> YahooFinanceProvider
```

## Goals

- Expose the existing agent through a small asynchronous HTTP API.
- Keep FastAPI isolated from LangChain message representations.
- Reuse one graph-execution service from the API and CLI.
- Preserve structured tool artifacts in the public response.
- Keep health checks independent of Gemini and Yahoo availability.
- Support deterministic, credential-free API tests.
- Retain extension points for later threads, streaming, and persistence.

## Non-goals

- Server-managed conversation history or thread endpoints.
- SSE, WebSockets, background jobs, or reconnectable streams.
- Authentication, users, workspaces, quotas, or public deployment hardening.
- PostgreSQL, Redis, or LangGraph checkpoint persistence.
- PTC, MCP, sandboxing, or subagent orchestration.
- A frontend.

## Decisions

### P3-001: Use a stateless research endpoint

The initial contract is:

```text
POST /api/v1/research
```

Each request contains one natural-language message and starts with fresh graph
state. A `/threads/{id}/messages` route would imply a thread lifecycle that
does not exist yet. Thread-oriented routes should be introduced together with
real persistence rather than using cosmetic identifiers.

### P3-002: Keep graph execution transport-neutral

`ResearchAgentService` accepts text, invokes the compiled graph, and returns a
`ResearchResult`. It owns the translation from LangChain messages into:

- Final assistant text.
- Model tool calls.
- Tool results used by the CLI trace.
- Structured tool artifacts.

FastAPI and the CLI share this service. HTTP routes therefore do not traverse
LangChain messages, and the graph does not construct HTTP responses.

### P3-003: Publish explicit Pydantic contracts

The API accepts `ResearchRequest` and returns `ResearchResponse`. Unknown
request fields are rejected, messages are trimmed, blank messages fail
validation, and input length is bounded.

Artifacts retain their Phase 2 versioned payloads. The API validates their
required envelope fields while allowing artifact-specific data fields.

### P3-004: Compose the production graph once per application

The FastAPI lifespan creates the Gemini model, default tools, compiled graph,
and `ResearchAgentService` once. The service is stored on application state
and injected into routes.

Tests pass a service containing a deterministic graph double. They therefore
do not instantiate Gemini or contact Yahoo.

If required Gemini configuration is absent, application liveness remains
available and research requests return a controlled `503`. This distinguishes
process health from research readiness without adding a separate readiness
endpoint in this phase.

### P3-005: Use stable controlled errors

Graph and model execution failures become:

```json
{
  "error": {
    "code": "research_failed",
    "message": "The research agent could not complete the request."
  }
}
```

Unavailable configuration or composition returns `503`; execution failure
returns `502`; unexpected API failures return a generic `500`. Internal
exception details and credentials are not placed in responses.

Expected Yahoo and symbol failures remain tool results, as decided in Phase 2.
They normally produce a successful HTTP response containing an explanatory
answer and error artifact.

### P3-006: Preserve correlation without logging payloads

Every HTTP response receives an `X-Request-ID`. The server logs the identifier,
method, path, status, and duration. Request content, complete provider
payloads, artifacts, and credentials are not logged by this middleware.

### P3-007: Keep the API non-streaming

The route awaits one complete graph result and returns JSON. This establishes
the application and error contracts before adding the substantially different
lifecycle required by SSE, cancellation, replay, and background execution.

LangAlpha's production endpoint streams thread turns through Redis and
PostgreSQL-backed lifecycle state. Reproducing only the visible SSE syntax
without those ownership guarantees would create a misleading abstraction.

## Verification

Credential-free tests cover:

- Health response and request correlation ID.
- Successful answer, tool-call, and artifact serialization.
- Input normalization and strict validation.
- Stable graph-failure responses.
- Liveness and `503` behavior when configuration is missing.
- Graph-message extraction into a transport-neutral result.

A live smoke check requires `GEMINI_API_KEY` and exercises:

```text
HTTP -> Gemini -> LangGraph -> Yahoo -> Gemini -> JSON
```

## Result

MiniAlpha now follows LangAlpha's server-side separation at learning scale:
the server is a delivery layer around an independently testable agent service.
It intentionally stops before LangAlpha's user, workspace, thread, run-ledger,
checkpoint, Redis, SSE, sandbox, and PTC architecture.
