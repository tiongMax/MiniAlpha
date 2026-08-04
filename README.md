# MiniAlpha

MiniAlpha is a learning project that rebuilds the core agent loop behind
LangAlpha with an explicit LangGraph instead of `langchain.agents.create_agent`.

## Phase 3

Phase 3 exposes the Phase 2 research stack through a small, stateless FastAPI
server:

```text
HTTP client
  -> FastAPI
  -> ResearchAgentService
  -> explicit LangGraph agent
  -> get_company_overview tool
  -> CompanyResearchService
  -> FinancialDataProvider protocol
  -> YahooFinanceProvider
  -> Yahoo Finance
```

The graph is still built explicitly without `langchain.agents.create_agent`:

```text
user -> model -> tool -> model -> final answer
```

Provider data is normalized into a `CompanyOverview` domain model before the
agent sees it. The tool returns both compact model-readable text and a
structured artifact containing raw numeric values, provider metadata, and a
schema version.

The API converts internal LangChain messages into a stable response containing
the final answer, model tool calls, and structured artifacts. Every request is
independent: there are no threads or server-managed conversation history yet.

There is no PostgreSQL, Redis, frontend, checkpoint persistence, streaming,
PTC, workspace, sandbox, MCP, or subagent orchestration yet.

## Setup

Copy `.env.example` to `.env`, then set:

```dotenv
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

Install dependencies and run the CLI:

```powershell
uv sync
uv run python cli.py
```

Run the API:

```powershell
uv run uvicorn app.api.main:app --reload
```

Interactive OpenAPI documentation is available at:

```text
http://127.0.0.1:8000/docs
```

Check liveness without calling Gemini or Yahoo:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Start the PostgreSQL development database used by persistent conversation
records and LangGraph checkpoints:

```powershell
docker compose up -d postgres
uv run python -m scripts.setup_database
```

The Compose service uses host port `5433` so it can run alongside a local
PostgreSQL installation on the default port. Copy the matching `DATABASE_URL`
from `.env.example` into `.env`.

Stop the container without deleting its data:

```powershell
docker compose down
```

Use `docker compose down -v` only when intentionally deleting the development
database volume.

Submit a research request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/v1/research `
  -ContentType application/json `
  -Body '{"message":"Analyze Apple."}'
```

The research endpoint returns:

```json
{
  "answer": "...",
  "tool_calls": [
    {
      "name": "get_company_overview",
      "arguments": {"symbol": "AAPL"}
    }
  ],
  "artifacts": [
    {
      "artifact_type": "company_overview",
      "schema_version": 1,
      "status": "ok",
      "data": {}
    }
  ]
}
```

Try:

```text
Analyze Apple.
Compare Apple and Microsoft using company facts.
Give me an overview of BRK-B.
Hello, what can you do?
```

Exercise Yahoo directly without using Gemini:

```powershell
uv run python -m scripts.smoke_company AAPL MSFT BRK-B
```

Run the credential-free test suite:

```powershell
uv run pytest
```

Run the cleanup and formatting gates:

```powershell
uv run ruff check .
uv run ruff format --check .
```

## Code map

```text
app/config.py                  model configuration
app/agent/state.py             shared graph state
app/agent/prompts.py           static agent instructions
app/agent/tools.py             tool factory and agent-facing formatting
app/agent/nodes.py             routing
app/agent/graph.py             explicit StateGraph construction
app/api/main.py                FastAPI application factory
app/api/routes/                health and stateless research routes
app/api/schemas.py             public HTTP contracts
app/domain/                    normalized data models and expected errors
app/providers/                 provider protocol and Yahoo implementation
app/services/company_research.py provider-neutral financial-data orchestration
app/services/research_agent.py transport-neutral graph execution
scripts/                       live provider smoke checks
cli.py                         interactive trace runner
```

Yahoo Finance data may be delayed, incomplete, or unavailable. MiniAlpha
preserves missing values as `None`/`N/A` and does not silently turn them into
zero.

## Architecture rationale

The [Phase 2 architecture decision log](docs/phase-2-decision-log.md) explains
why the provider, service, tool, artifact, error, formatting, prompting,
typing, and testing boundaries were chosen, including rejected alternatives
and intentionally deferred work.

The [Phase 3 architecture decision log](docs/phase-3-decision-log.md) explains
the stateless HTTP contract, shared graph runner, lifecycle composition, and
error-handling boundaries.

The [Phase 3 API guide](docs/phase-3-api.md) documents endpoint contracts,
examples, validation, errors, request correlation, and current limitations.
