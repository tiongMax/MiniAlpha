# Phase 6 event protocol

MiniAlpha exposes application-owned events rather than raw LangGraph callbacks.
Every event uses this envelope:

```json
{
  "event_id": 1,
  "event": "metadata",
  "run_id": "uuid",
  "thread_id": "uuid",
  "timestamp": "2026-08-04T12:00:00+00:00",
  "data": {}
}
```

`event_id` starts at 1 and increases within one HTTP stream. Phase 6 IDs are
not durable reconnect cursors; Redis-backed replay is deferred to Phase 8.

## Events

- `metadata`: first event; contains `turn_index` and `replayed`.
- `progress`: contains a stable execution `phase` (`planning`,
  `running_tools`, or `synthesizing`), a user-facing message, and optionally
  the tool names selected for the current batch.
- `message_chunk`: contains a new assistant-text `delta`.
- `tool_call`: contains `tool_call_id`, tool `name`, and complete `arguments`.
- `tool_result`: contains the correlated call ID, name, status, and compact
  model-facing summary.
- `artifact`: contains the existing versioned artifact envelope.
- `error`: contains a sanitized run-level error code and message.
- `run_end`: last event; contains authoritative `completed` or `error` status.

Expected Yahoo or ticker limitations remain successful graph executions. They
appear as error-status tool evidence and can still end with a completed run.
Model, graph, or checkpoint-publication failures produce `error` followed by
`run_end` with error status.

## Ordering and durability

Intermediate events are provisional. On success, the graph completes, the
answer/tool calls/artifacts and checkpoint pointer are committed, and only then
is `run_end(status=completed)` emitted. PostgreSQL remains lifecycle truth.

Completed idempotent retries produce a synthetic stream from stored output and
do not call the model again. Original token boundaries and tool results are not
reconstructed because Phase 6 does not persist individual events.
