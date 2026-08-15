# Exact and semantic cache evaluation

Point 2 caches only complete, successful **stateless** `ResearchResult`s. A
cache hit therefore skips the LangGraph planning and synthesis calls; caching
Yahoo responses alone would not substantiate a generation-token reduction.
Durable thread turns always execute because a hit would otherwise fail to
advance their checkpoint.

## Production path

1. Normalize the request and namespace it by generation model, prompt hash,
   graph/tool-schema versions, policy version, embedding model, and dimension.
2. Look up the exact key in Redis.
3. For conservatively eligible queries, embed with Gemini and search pgvector
   only inside identical ticker, intent, period, interval, date, and parameter
   constraints.
4. Execute the origin graph on a miss.
5. Cache only non-empty results whose tool calls and artifacts all succeeded.
6. Apply the shortest data-specific TTL from the artifact source timestamp.

Relative-time language such as `latest`, `today`, or `now` suppresses semantic
reuse and clamps exact caching to at most 60 seconds. Company news uses five
minutes; overview/ratios/comparison 15 minutes; SEC/insider data 30 minutes;
price-derived analytics 60 minutes; estimates six hours; and statements and
ownership 24 hours. Error, partial, timestamp-free, stale, malformed, or
oversized responses are never cached.

Redis, embedding, pgvector, malformed-entry, and cache-write failures are
fail-open. Redis fill locks use expiring ownership tokens, so concurrent exact
misses perform one origin generation when the owner completes within the
bounded wait.

## Locked deterministic experiment

- Corpus: `evals/cache/cases_v1.json`
- Cases: 13 sequential requests
- SHA-256: `dcbdd6bdcdce72ab1415a4f904e64a515e99b6e8ec4ff7b384c9b1892fee0195`
- Raw report: `evals/results/cache_v1.json`
- Origin: deterministic complete results with 100 generation tokens and a
  measured 10 ms delay per call
- Embeddings: deterministic local vectors for repeatability; production uses
  768-dimensional Gemini embeddings

The workload includes cold requests, exact repeats, valid paraphrases,
adversarial symbol/period near misses, relative-time suppression, and repeated
provider errors.

| Metric | Cache disabled | Cache enabled |
|---|---:|---:|
| Requests | 13 | 13 |
| Generation tokens | 1,300 | 800 |
| Correct cache-policy outcomes | 13 | 13 |
| Exact hits | 0 | 3 |
| Semantic hits | 0 | 2 |
| False semantic hits | 0 | 0 |

Measured generation-token reduction was **38.5%**. Warm exact-hit median
latency was **0.279 ms**, compared with **15.210 ms** for cache-enabled cold
misses in this local synthetic run.

These figures verify the control flow and measurement accounting, but they are
not a production Gemini/Yahoo/Redis latency benchmark. In particular, they do
not substantiate the original résumé values of **28%** and **2.1 s to 120 ms**.
Those numbers require a separately frozen live workload against deployed Redis,
pgvector, Gemini embeddings, and Gemini generation. Until that run exists, use
the measured deterministic result only with its scope stated clearly.

