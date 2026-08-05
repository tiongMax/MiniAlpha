# Phase 10 structured financial artifacts

Phase 10 replaces raw JSON as the normal frontend presentation for supported
financial evidence. The generic envelope remains stable:

```text
artifact_type + schema_version + status + data/error
```

## Supported renderers

- `company_overview` version 1 renders identity, classification, price,
  valuation, revenue, growth, margins, yield, source, and retrieval time.
- `price_history` version 1 renders an SVG closing-price chart, range summary,
  period return, observation count, source, and retrieval time.
- When one turn contains two or more valid company-overview artifacts, the UI
  derives a side-by-side comparison table without parsing model prose.
- Unknown versions and error artifacts retain the inspectable JSON fallback.

The new `get_price_history` tool accepts a bounded period (`1mo`, `3mo`,
`6mo`, `1y`, `2y`, or `5y`) and interval (`1d`, `1wk`, or `1mo`). Yahoo's
blocking history request runs outside the event loop and is normalized into
provider-neutral OHLCV points before entering the artifact envelope.

Artifacts are still persisted in PostgreSQL and replayed through the same
Redis/SSE protocol. The model receives only the compact price summary; the
complete point series stays in the structured artifact rather than model
prose.
