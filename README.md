# MiniAlpha

MiniAlpha is a learning project that rebuilds the core agent loop behind
LangAlpha with an explicit LangGraph instead of `langchain.agents.create_agent`.

## Phase 1

Phase 1 contains only:

- a `messages` graph state;
- one model node;
- one `ToolNode`;
- conditional routing from the model to tools or `END`;
- one deterministic fake financial tool; and
- a CLI that prints each graph update.

The deliberately small scope isolates the core agent loop:

```text
user -> model -> tool -> model -> final answer
```

There is no FastAPI, PostgreSQL, Redis, frontend, real market-data provider,
checkpoint persistence, PTC, or subagent orchestration yet.

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
Compare Apple and Microsoft.
Hello, what can you do?
```

Run the focused tests:

```powershell
uv run pytest
```

## Code map

```text
app/config.py        model configuration
app/agent/state.py   shared graph state
app/agent/prompts.py static agent instructions
app/agent/tools.py   deterministic fake company tool
app/agent/nodes.py   routing
app/agent/graph.py   explicit StateGraph construction
cli.py               interactive trace runner
```
