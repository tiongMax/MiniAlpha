# MiniAlpha

MiniAlpha is a learning project that rebuilds the core agent loop behind
LangAlpha with an explicit LangGraph instead of `langchain.agents.create_agent`.

## Phase 2

Phase 2 keeps the custom agent loop from Phase 1 and replaces the fake
AAPL/MSFT dictionary with a real, provider-neutral research path:

```text
LangGraph agent
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

There is no FastAPI, PostgreSQL, Redis, frontend, checkpoint persistence, PTC,
workspace, or subagent orchestration yet.

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

## Code map

```text
app/config.py        model configuration
app/agent/state.py   shared graph state
app/agent/prompts.py static agent instructions
app/agent/tools.py   tool factory and agent-facing formatting
app/agent/nodes.py   routing
app/agent/graph.py   explicit StateGraph construction
app/domain/          normalized data models and expected errors
app/providers/       provider protocol and Yahoo implementation
app/services/        provider-neutral research orchestration
scripts/             live provider smoke checks
cli.py               interactive trace runner
```

Yahoo Finance data may be delayed, incomplete, or unavailable. MiniAlpha
preserves missing values as `None`/`N/A` and does not silently turn them into
zero.

## Architecture rationale

The [Phase 2 architecture decision log](docs/phase-2-decision-log.md) explains
why the provider, service, tool, artifact, error, formatting, prompting,
typing, and testing boundaries were chosen, including rejected alternatives
and intentionally deferred work.
