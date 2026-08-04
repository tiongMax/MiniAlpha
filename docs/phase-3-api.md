# Phase 3 API guide

MiniAlpha Phase 3 exposes one liveness endpoint and one stateless financial
research endpoint. The generated OpenAPI interface is available at `/docs`
while the server is running.

## Start the server

Set `GEMINI_API_KEY` in `.env`, then run:

```powershell
uv run uvicorn app.api.main:app --reload
```

The development server listens on `http://127.0.0.1:8000`.

## `GET /health`

Reports process liveness without calling Gemini or Yahoo Finance.

Response:

```json
{
  "status": "ok",
  "service": "mini-alpha",
  "phase": 3
}
```

A healthy response means the HTTP process is available. It does not guarantee
that Gemini credentials or Yahoo Finance are currently usable.

## `POST /api/v1/research`

Executes one independent request through the explicit LangGraph agent.

Request:

```json
{
  "message": "Compare Apple and Microsoft using verified company facts."
}
```

`message` is trimmed before execution. It must contain between 1 and 10,000
characters after trimming. Unknown request fields are rejected.

Successful response:

```json
{
  "answer": "Apple and Microsoft are both large profitable companies...",
  "tool_calls": [
    {
      "name": "get_company_overview",
      "arguments": {
        "symbol": "AAPL"
      }
    },
    {
      "name": "get_company_overview",
      "arguments": {
        "symbol": "MSFT"
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
  ]
}
```

`answer` is the final model response. `tool_calls` records the tools and
arguments selected by the model. `artifacts` contains normalized, versioned
evidence returned by tools.

Expected ticker failures remain agent-level results:

```json
{
  "artifact_type": "company_overview",
  "schema_version": 1,
  "status": "error",
  "error": "No company data is available for UNKNOWN."
}
```

They normally return HTTP `200` because the graph completed and explained the
data limitation.

## Error responses

Controlled server failures use one envelope:

```json
{
  "error": {
    "code": "research_unavailable",
    "message": "The research service is not configured."
  }
}
```

| Status | Code | Meaning |
|---|---|---|
| `422` | FastAPI validation detail | Request JSON does not match the contract |
| `502` | `research_failed` | Gemini or graph execution did not complete |
| `503` | `research_unavailable` | Required configuration or composition is unavailable |
| `500` | `internal_error` | Unexpected server failure |

Internal exception details, provider payloads, and credentials are not
included in error responses.

## Request correlation

Every response contains an `X-Request-ID` header. Server logs include this ID,
the HTTP method, route, status, and duration. They do not include request
content or complete financial-data artifacts.

## Stateless behavior

Every request starts with fresh graph state. MiniAlpha does not remember a
previous API request:

```text
Request 1: Analyze Apple.
Request 2: Now compare it with Microsoft.
```

The second request does not know what “it” means. Thread IDs, checkpoints, and
conversation history are intentionally deferred to a persistence phase.

## Current limitations

Phase 3 does not include:

- Authentication or authorization.
- Rate limiting or quotas.
- Server-managed threads.
- Streaming responses.
- Redis or PostgreSQL.
- Public-internet deployment hardening.
- PTC, MCP, or sandbox execution.

Bind the development server to localhost unless those controls are added.
