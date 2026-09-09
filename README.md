# 📈 MiniAlpha

MiniAlpha is a learning project that rebuilds the core research loop behind [LangAlpha] with an explicit **LangGraph** instead of `langchain.agents.create_agent`.

---

## ✨ Features & Architecture

### Phase 12: Deterministic Quantitative Research
Phase 12 builds on the detached, reconnectable run lifecycle, structured artifacts, and fundamental tools with deterministic market calculations:

```text
HTTP client
  -> FastAPI
  -> DetachedRunManager (one background worker)
       -> ThreadResearchService
       -> conversation repository -> PostgreSQL application tables
       -> ResearchAgentService -> explicit LangGraph
                                  -> PostgreSQL checkpoints
                                  -> financial data tools -> Yahoo Finance
                                  -> quantitative service -> application calculations
                                  -> application event translator -> Redis Streams -> SSE
  -> React artifact renderer -> company cards, charts, comparison tables
```

- **Persistence Boundary:** The application database (PostgreSQL) owns request identity, run lifecycle, transcripts, and checkpoint pointers. LangGraph owns the serialized graph state. 
- **Frontend integration:** A React frontend consumes the API for interactive agent testing. It supports durable threads, streaming assistant text, historical price charts, and data tools.
- *(Note on Statelessness)*: The original stateless endpoint remains available. Each call initiates a fresh graph state.

---

## 🚀 Setup & Quickstart

### 1. Environment Configuration

Copy `.env.example` to `.env`, then configure your keys:

```dotenv
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
DATABASE_URL=postgresql://minialpha:minialpha@localhost:5433/minialpha
REDIS_URL=redis://localhost:6379/0
```
>*Optional:* To export stable LangSmith traces without payloads, set `LANGSMITH_TRACING=true` and your `LANGSMITH_API_KEY`.

### 2. Install Dependencies

You'll need `uv` installed. Run the following to sync the virtual environment:
```powershell
uv sync
```

### 3. Initialize Services

Start PostgreSQL and Redis in the background, then initialize the database schema:
```powershell
# 1. Start containers
docker compose up -d postgres redis

# 2. Run migrations (resolves Heads and builds tables)
uv run python -m scripts.setup_database
```
> **Note:** Use `docker compose down -v` only when intentionally deleting MiniAlpha's development database volume!

### 4. Run the Application

Start the FastAPI application:
```powershell
uv run python -m scripts.run_api --reload
```

In a second terminal, start the React frontend:
```powershell
cd frontend
npm install
npm run dev
```
Open **[http://127.0.0.1:5173](http://127.0.0.1:5173)** to interact with the agent. 

---

## 🧪 Verification & Best Practices

Run the credential-free suite and code-quality gates to verify system health:

```powershell
# Run the test suite (disable cache if using Windows to avoid PermissionErrors)
uv run pytest -p no:cacheprovider

# Code Quality
uv run ruff check .
uv run ruff format --check .
```
*(Tests for PostgreSQL integration are skipped unless their explicit test environment variable is enabled.)*

---

## 💻 API & Research Workflows

### Durable Research (With UI)
You can directly interact via API for persistent multi-turn conversations:

```powershell
$requestKey = [guid]::NewGuid()
$first = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/threads/messages -ContentType application/json -Body (@{ messages = @(@{ role = "user"; content = "Analyze Apple." }); request_key = $requestKey } | ConvertTo-Json -Depth 4)
```
Streams emit `metadata`, `message_chunk`, `tool_call`, `tool_result`, `artifact`, `error`, and `run_end`. The older stream endpoints are available as wrappers for detached execution.

### Stateless Research & CLI
Submit an independent stateless request:
```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/research -ContentType application/json -Body '{"message":"Analyze Apple."}'
```
Or use the prompt interface via CLI:
```powershell
uv run python cli.py
```
*(Exercise Yahoo independently without Gemini: `uv run python -m scripts.smoke_company AAPL MSFT BRK-B`)*

---

## 🗺️ Code Map
```text
app/config.py                    model and database configuration
app/agent/                       explicit graph, state, tools, and routing
app/api/main.py                  FastAPI factory, lifespan, and error mapping
app/api/routes/                  health, readiness, stateless, and thread routes
app/api/schemas.py               strict public HTTP contracts
app/domain/                      normalized company and fundamental datasets
app/persistence/                 repository contract and memory/Postgres adapters
app/providers/                   provider protocol and Yahoo implementation
app/services/                    quantitative and company research orchestration
app/events/store.py              Redis Streams replay and test event transport
frontend/src/artifacts/          typed artifact guards and financial renderers
migrations/                      application-owned PostgreSQL schema
scripts/                         database initialization and API launchers
cli.py                           interactive stateless trace runner
```

---

## 📖 Architecture Rationale

For historical decision logs encompassing system iterations, refer to:
- [Phase 2 decision log](docs/phase-2-decision-log.md)
- [Phase 3 decision log](docs/phase-3-decision-log.md)
- [Phase 3 API guide](docs/phase-3-api.md)
- [Phase 4–5 decision log](docs/phase-4-5-decision-log.md)
- [Phase 4–5 API guide](docs/phase-4-5-api.md)
- [Frontend architecture and run guide](docs/frontend.md)
- [Phase 7 API and lifecycle guide](docs/phase-7-api.md)
- [Phase 8 Redis reconnect guide](docs/phase-8-api.md)
- [Phase 10 structured artifact guide](docs/phase-10-artifacts.md)
- [Phase 11 fundamental research guide](docs/phase-11-fundamentals.md)
- [Phase 12 quantitative research guide](docs/phase-12-quantitative.md)
