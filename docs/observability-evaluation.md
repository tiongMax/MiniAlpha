# Observability contract evaluation

Point 4 adds stable application spans around the research pipeline so latency,
model usage, cache behavior, and failures can be attributed to a specific step.
LangChain and LangGraph may emit additional native traces; the names below are
the MiniAlpha application contract and should remain stable across refactors.

| Category | Stable span name | Required safe metadata |
|---|---|---|
| Research run | `mini_alpha.research_run` | `outcome`, `duration_ms` |
| Intent routing | `routing.decision` | `selected_tool_count` |
| Model call | `model.invoke` | `attempt`, input/output/total tokens |
| Tool call | `tool.execute` | `attempt`, `outcome` |
| Exact cache | `cache.exact` | `cache_status`, `cache_tier` |
| Embedding | `cache.embedding` | `cache_status`, `cache_tier` |
| Semantic cache | `cache.semantic` | `cache_status`, `cache_tier` |
| Data provider | `provider.request` | `provider_operation`, `attempt` |
| Persistence | `persistence.finalize` | `persistence_operation` |

Error spans additionally require a stable, sanitized `error_type`. They do not
carry raw exception messages, prompts, symbols, provider URLs, credentials, or
connection strings.

## Credential-free validation

The locked fixture at `evals/observability/scenarios_v1.json` contains generic
span records for six synthetic trajectories:

- routed tool/provider success;
- exact-cache and semantic-cache hits;
- a provider failure followed by a degraded answer;
- a transient model failure followed by a successful retry; and
- a terminal persistence failure.

The evaluator verifies more than the presence of span names. Every non-root
span must reach the single research root through an existing, acyclic parent
chain, child timing must fit inside its parent, and provider calls must be
children of tool calls. Every span needs numeric latency attribution. Every
model span needs non-negative, additive token counts. Observed error spans must
exactly match the fixture's expected failures and include safe failure metadata.

The privacy scan rejects prompt/query/symbol/credential metadata keys and scans
all tags and attributes for 17 locked raw-prompt, ticker, secret, connection,
and provider-URL markers. It reports only the location of a violation and does
not copy the observed value into its output.

Run the evaluator without Gemini, Yahoo Finance, Redis, PostgreSQL, or LangSmith
credentials:

```console
uv run python scripts/evaluate_observability.py
uv run pytest tests/evals/test_observability_evaluator.py -q
```

## Locked result

- Fixture SHA-256:
  `1f50b66f22c5ef9cb685fd8694b6d0df45d06086af69ffe496329c0583bbeaf3`
- Raw report: `evals/results/observability_v1.json`
- Scenarios: 6
- Synthetic spans: 34
- Required span categories covered: 9 of 9
- Valid parent links: 28 of 28
- Spans with numeric latency: 34 of 34
- Model spans with numeric, additive usage: 7 of 7
- Correctly attributed error spans: 5 of 5
- Forbidden metadata/marker hits: 0
- Contract violations: 0

## Evidence boundary

This is a deterministic **instrumentation contract and coverage** evaluation
over synthetic span records. It proves that the locked records satisfy the
stable naming, hierarchy, numeric attribution, failure, and privacy rules, and
it gives runtime tests a provider-independent oracle.

It does **not** upload or inspect production LangSmith traces, establish that a
deployed process reaches every instrumented path, validate a LangSmith project
or dashboard, measure tracing overhead, or demonstrate any runtime-performance
improvement. Those claims require an environment-backed smoke run followed by
inspection of the exported LangSmith trace IDs. The defensible résumé wording
is therefore about adding step-level instrumentation and a credential-free
contract test; do not imply observed production coverage unless those uploaded
traces are retained as evidence.
