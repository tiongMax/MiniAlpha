# MiniAlpha evaluation framework

This package measures MiniAlpha behavior without changing the production graph. The
default suite is credential-free and uses synthetic frozen outcomes. Those outcomes
verify the runner, graders, reporting, and CI gates; they are **not** a measurement of
the live Gemini/Yahoo configuration.

## Case contract

`cases/v1.json` contains 20 cases across company/fundamental retrieval,
deterministic quantitative calculations, multi-company comparison, multi-step
research, missing/invalid data, and multi-turn follow-ups. Each case declares:

- identity, category, question, difficulty, entities, symbols, and optional turns;
- required, optional, and forbidden tools;
- expected normalized arguments;
- required answer elements and structured numerical expectations with tolerances;
- a frozen fixture reference and deterministic grader switches.

To add a case, copy an existing case object, choose a unique `case_id`, add the
matching fixture to `fixtures/v1.json`, and run the focused tests. Keep provider
values frozen and label synthetic data explicitly.

## Commands

Run the full credential-free suite:

```powershell
uv run python -m evals run
```

Run one case:

```powershell
uv run python -m evals run --case returns-aapl
```

Run repeated trials (the frozen executor is intentionally identical; a live executor
may vary):

```powershell
uv run python -m evals run --repeats 3 --configuration frozen-repeat
```

Compare a candidate configuration against a baseline and fail when a configured
regression is exceeded:

```powershell
uv run python -m evals compare `
  evals/results/baseline.json `
  evals/results/candidate.json `
  --thresholds evals/thresholds.json
```

The comparison command returns exit code 1 on regression, which is suitable for CI.
Thresholds are absolute metric changes. For percentage-valued rates, `0.02` means
two percentage points.

## Live evaluation

`LiveResearchServiceExecutor` is a read-only adapter for an already-constructed
`ResearchAgentService`. Live execution is deliberately not the CLI default because it
requires external credentials and mutable provider data. The stateless adapter rejects
multi-turn cases; use a checkpoint-aware executor for those cases.

Executors return `ExecutionOutcome`. When a provider does not expose token or cost
usage, leave those fields as `null`. Never estimate or invent them. An optional LLM
judge can implement the `LLMJudge` protocol and must return structured scores for
completeness, evidence-based interpretation, and appropriate uncertainty together with
its judge model/version. The deterministic suite does not instantiate a judge.

## Outputs and metrics

Each run writes raw JSON trajectories and a Markdown report. Trial records retain the
case/trial IDs, configuration labels, prompt/graph versions, tool calls and normalized
arguments, tool results, artifacts, final answer, errors, elapsed time, and nullable
usage/cost fields.

Reports show overall and category-level values rather than one opaque score:

- tool-selection precision, recall, and F1;
- tool-argument accuracy;
- numerical accuracy with tolerance and unit normalization;
- required-answer-element completion;
- duplicate and unnecessary call counts;
- elapsed time and available token/cost values.

Failure reasons and raw trajectories are retained for diagnosis. Numerical grading
reads structured artifacts, supports decimal/percent/basis-point and
thousand/million/billion normalization, and does not scrape prose when exact values are
available.

## Reproducibility limitations

The frozen suite is reproducible but synthetic. It does not establish current live-agent
quality, human-versus-LLM-judge agreement, latency, or cost. A real baseline requires a
separately named live configuration, captured provider snapshots, repeated trials, and
honest reporting of the environment and any unavailable usage metadata.

