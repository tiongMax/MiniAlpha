# MiniAlpha frontend

MiniAlpha copies the useful shape of LangAlpha's frontend without copying its
product-scale features. The frontend is a React 19, TypeScript, Vite, and
TanStack React Query single-page application under `frontend/`.

## What was copied from LangAlpha

LangAlpha separates its chat path into four responsibilities:

1. Components render UI and initiate user actions.
2. An API module owns HTTP requests.
3. Run admission uses `POST`; event attachment uses streaming `GET` with
   `fetch()` and `ReadableStream`.
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
            -> POST run admission
            -> GET text/event-stream
            -> POST cancellation
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

## Reconnect behavior

The backend owns execution after admission, so disconnecting the browser does
not cancel the run. The client remembers the greatest application `event_id`
it reduced and reconnects the GET request with `Last-Event-ID`. Redis replays
only later events. The Stop action calls the server cancellation endpoint and
waits for a durable `cancelled` terminal event.

Authentication, workspaces, dashboards, market WebSockets, file panels,
subagents, and human approval remain deliberately omitted.

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
