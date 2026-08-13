# Intent-routing evaluation

This branch replaces MiniAlpha's fixed 16-tool binding with a deterministic,
request-scoped router. The router exposes a narrow tool subset for explicit
intents, no tools for conceptual questions, a bounded evidence bundle for
simple named-company requests, and all tools only when narrowing is unsafe.

## Locked experiment

- Dataset: `evals/routing/cases_v1.json`
- Queries: 100 unique labeled prompts
- Dataset SHA-256: `f6f7f97b85eca8766c6ddf5c82c13af022ba54d73dbd6c462f5818354445e73a`
- Provider: deterministic local financial data
- Model: `gemini-3.5-flash-lite`
- Treatment variable: fixed 16 schemas versus request-scoped schemas
- Raw trajectories: `evals/results/intent_routing_v1.json`
- Run date: 2026-08-13

The selection-error definition is fixed before grading: a trial is an error if
it fails to complete, omits a required successful tool, executes a successful
tool outside the required/optional labels, or duplicates an equivalent call.
The evaluator also reports completed-run selection errors separately so model
or client failures are not confused with tool-selection behavior.

## First live result

| Metric | Fixed 16 | Intent routed |
|---|---:|---:|
| Scheduled trials | 100 | 100 |
| Completed trials | 99 | 96 |
| All-trial selection errors | 14 (14.0%) | 12 (12.0%) |
| Completed-run selection errors | 13/99 (13.1%) | 8/96 (8.3%) |
| Mean schemas exposed | 16.00 | 2.11 |

Among the 95 pairs where both variants completed, routing corrected seven
fixed-binding errors and introduced two errors; six pairs failed selection in
both configurations and 80 passed both. Schema exposure fell 86.8%.

The routed model had four client/model failures versus one for the baseline in
this single repeat. Several trials spent a long time in bounded client retries,
so wall-clock latency from this concurrent run is not a clean routing metric.
Additional repeats should be run before treating the result as a stable model
quality estimate.

## Router refinement after the run

The raw run showed that the six deliberately vague company prompts triggered
the safe all-tool fallback and accounted for most unnecessary calls in both
variants. The router now recognizes only tightly bounded named-company forms
and maps them to overview, overview plus ratios for financial-health prompts,
or overview plus news for `right now`. Unknown requests such as a company
"moat" question still fall back to all tools because no current tool can
directly verify that claim.

The deterministic corpus preflight after this refinement exposes 1.39 schemas
on average, has one all-tool fallback, and never omits a labeled required tool.
This refinement has not yet been remeasured with the live model; the committed
raw result remains the pre-refinement evidence.

## Résumé conclusion

The repository currently supports an honest statement that intent routing
reduced completed-run selection errors from **13.1% to 8.3%** in this locked
100-query run while reducing exposed schemas by **86.8%**. It does **not**
support the original **21% to 9%** wording. That number should not be used
unless a future locked, repeated experiment actually measures it.

