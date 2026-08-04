# Phase 6 streaming API

The Phase 5 JSON endpoints remain unchanged. Phase 6 adds:

```text
POST /api/v1/threads/messages/stream
POST /api/v1/threads/{thread_id}/messages/stream
```

Both accept the existing `ThreadMessageRequest` body and return
`text/event-stream`. Because submission uses `POST`, browser clients should use
streaming `fetch()` rather than the GET-only native `EventSource` interface.

Each frame contains an SSE ID, event name, and JSON envelope:

```text
id: 1
event: metadata
data: {"event_id":1,"event":"metadata",...}

```

Responses disable caching and reverse-proxy buffering. Admission happens before
the response opens, so validation, missing-thread, request-key, and active-run
conflicts retain their ordinary JSON HTTP errors. Failures after streaming has
begun are represented in-band by `error` and `run_end`.

Phase 6 remains attached execution. A disconnected client cannot reconnect to
missed events, and the server cannot promise that execution survives the
disconnect. Detached execution and cancellation are Phase 7; Redis cursors and
replay are Phase 8.
