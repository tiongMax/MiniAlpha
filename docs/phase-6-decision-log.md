# Phase 6 decision log

## P6-001: Own the public event protocol

MiniAlpha translates LangGraph messages and state updates into seven stable
application events. Provider and LangGraph callback shapes are private.

## P6-002: Stream over POST

The existing validated JSON request is retained and the response uses SSE
framing. Future React code will consume it with streaming `fetch()`.

## P6-003: Admit before opening the stream

Durable admission runs before response headers so known request conflicts keep
their existing HTTP semantics. Execution begins only while iterating the body.

## P6-004: Preserve the Phase 5 commit boundary

The durable service, not the route, owns finalization. Successful `run_end` is
created only after `complete_run` commits the result and publishes the new
checkpoint head.

## P6-005: Keep Phase 6 attached and ephemeral

Events are delivered directly to one HTTP response. Redis, reconnect cursors,
detached workers, cancellation, and abandoned-run recovery are deliberately not
introduced early.

## P6-006: Synthesize completed replays

An idempotent replay emits stored calls, artifacts, and the stored answer as one
chunk, followed by `run_end`. It does not re-execute Gemini or Yahoo Finance.
