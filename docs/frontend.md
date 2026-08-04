# MiniAlpha frontend

MiniAlpha copies the useful shape of LangAlpha's frontend without copying its
product-scale features. The frontend is a React 19, TypeScript, Vite, and
TanStack React Query single-page application under `frontend/`.

## What was copied from LangAlpha

LangAlpha separates its chat path into four responsibilities:

1. Components render UI and initiate user actions.
2. An API module owns HTTP requests.
3. Streaming POST requests use `fetch()` and `ReadableStream`, because the
   browser's native `EventSource` only supports GET.
4. Stream events are reduced into UI state instead of exposing raw transport
   details throughout the component tree.
5. Committed server state uses React Query with hierarchical query keys and
   prefix invalidation.

MiniAlpha follows the same flow:

```text
App.tsx
  -> useResearchChat.ts
       -> React Query
            -> cached thread list and committed transcripts
       -> api/client.ts
            -> POST application/json
            <- text/event-stream
       -> chat/events.ts
            -> ChatTurn state
  -> message, tool, artifact, and status components
```

This keeps MiniAlpha's application event protocol as the boundary. React never
sees LangGraph callbacks or LangChain message objects.

React Query owns durable server state only. The active SSE response remains
local reducer state because streamed chunks and running tool cards are
provisional. After the run completes, `useResearchChat` refetches the committed
transcript and replaces that provisional state with the authoritative response.
Query keys live in `src/lib/queryKeys.ts`, following LangAlpha's centralized
key-factory pattern.

## Deliberately omitted

LangAlpha also includes authentication, workspaces, dashboards, market
WebSockets, file panels, subagents, human approval, reconnect readers, and
cancellation. MiniAlpha does not need those to test its current agent.

The current backend is still Phase 6 attached execution. The frontend therefore
does not expose a Stop action: aborting only the browser request is not durable
cancellation and could strand an `in_progress` run. A real Stop button should be
added with Phase 7's cancellation endpoint. Reconnection state should be added
with Phase 8's durable event IDs and replay endpoint.

## Run locally

Start the API and its PostgreSQL dependency from the repository root, then run:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to
`http://127.0.0.1:8000` by default. Override it when necessary:

```powershell
$env:VITE_PROXY_BACKEND = "http://127.0.0.1:9000"
npm run dev
```

For a production build:

```powershell
npm run build
```
