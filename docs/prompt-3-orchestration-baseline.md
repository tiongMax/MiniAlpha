# Prompt 3 orchestration baseline

Baseline commit: `3df19271f3a08654ac28754b344c0b16d360b6ba`

The pre-change graph was a generic `model -> tools -> model` loop. It already
supported parallel tool calls through LangGraph's `ToolNode`, model/tool
timeouts, detached execution, cancellation at the application boundary, and
structured tool artifacts. It did not have an application-owned capability
registry, deterministic pre-execution argument validation, structured plans,
within-run deduplication, evidence-sufficiency state, or explicit tool/retry
budgets.

## Credential-free baseline

Command:

```powershell
uv run --frozen pytest -p no:cacheprovider `
  tests/test_agent_routing.py tests/test_graph.py tests/test_tools.py -q
```

Result on 2026-08-10: `9 passed in 5.87s`.

This is a focused behavioral regression baseline, not a measurement of tool
selection accuracy, latency improvement, cost, or recovery rate. Those values
remain unmeasured until representative trajectory fixtures and comparison
configurations are available.

## First vertical slice

The initial Prompt 3 slice introduces a versioned registry for all 16 tools and
validates proposed arguments before any provider call. Calls that fail known
deterministic constraints become ordinary error tool results with structured
repair guidance. Other valid calls from the same model response still execute,
and their results retain the model's original call order.

Next slices should add the structured request router/planner, within-run call
deduplication, bounded retry policies, evidence sufficiency, and trace-level
budgets/metrics using this registry as the shared contract.
