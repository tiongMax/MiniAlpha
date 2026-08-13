# Structured failure recovery evaluation

Point 3 converts recoverable dependency failures into safe, versioned artifacts
the model can reason over. Each tool call has its own deadline and retry budget,
so one slow sibling no longer cancels the entire tool batch. Transient model
timeouts receive one retry. Cache operations and Redis event delivery fail open;
PostgreSQL admission/checkpoint/finalization failures remain terminal because
continuing would violate durable thread consistency.

## Failure artifact

The legacy public `error` string remains for compatibility. New error artifacts
also carry `failure` fields: stable code/category/source/operation, retryability,
attempt and maximum attempts, recovery state, and tool-call identity. Raw
exception messages, provider bodies, URLs, credentials, prompts, SQL, and stack
traces are never included. The object survives in-memory/PostgreSQL persistence
and API replay via migration `005_structured_failures`.

## Runtime recovery boundaries

- Tool calls execute concurrently but time out and fail independently.
- Read-only transient provider failures retry at most twice in total.
- Existing tools that return retryable error artifacts are retried too; retry
  accounting does not depend on an exception escaping the wrapper.
- Malformed arguments and unknown tools return `model_correction` artifacts and
  are not automatically repeated unchanged.
- Persistent failures become `exhausted` or `degraded` artifacts, allowing the
  model to produce an explicit qualified answer.
- Redis event publish exceptions and hangs are recorded in bounded, secret-free
  diagnostics and replayed from a bounded local fallback. They cannot change a
  durably completed run to `worker_failed`.
- Persistence errors, unresolved checkpoint conflicts, worker crashes, and
  process interruption remain terminal.

## Locked 50-case evaluation

- Corpus: `evals/failures/cases_v1.json`
- SHA-256: `19f1e081f6ec16911eb74dab9eeb4e2262ecf7c7d5474c386833c292ae72543c`
- Raw output: `evals/results/failure_recovery_v1.json`
- Categories: 14 provider/tool, 10 malformed input, 6 cache, 6 model,
  4 Redis event, 8 PostgreSQL/checkpoint, and 2 worker/process scenarios

The denominator is always all 50 scheduled original requests. Retries never
become extra requests, and terminal errors are not counted as usable completion.

| Metric | Baseline policy | Recovery policy |
|---|---:|---:|
| Scheduled original requests | 50 | 50 |
| Usable completions | 20 (40%) | 37 (74%) |
| Policy-correct outcomes | 20 (40%) | 50 (100%) |
| Durable completions | 20 | 37 |
| Delivery completions | 20 | 33 |
| Cascading failures | 17 | 0 |
| Structured error artifacts | 0 | 24 |

This report is a deterministic **policy simulation**, backed by executable unit
and integration tests for the actual graph, retry, persistence, cache, and event
boundaries. It is useful for auditing denominators and expected recovery paths;
it is not a live fault-injection run through every external service.

Accordingly, it does not substantiate the résumé's **62% to 92%** wording. A
fully defensible number requires a runner that actually injects each of these 50
faults into the deployed stack and records raw trajectories. Until that exists,
describe the implemented behavior and tested scenarios without citing the
original percentage, or explicitly label the 40%→74% result as policy-simulation
evidence rather than observed production completion.
