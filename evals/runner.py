"""Evaluation execution, raw trajectory capture, and grading orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from evals.graders import grade_trial
from evals.reporting import summarize
from evals.schema import (
    EvaluationCase,
    ExecutionOutcome,
    GradeResult,
    TrialRecord,
)


class EvaluationExecutor(Protocol):
    """Pluggable execution boundary for frozen or live configurations."""

    async def execute(
        self, case: EvaluationCase, *, trial_index: int
    ) -> ExecutionOutcome:
        """Execute one case and return a provider-independent outcome."""
        ...


class LLMJudge(Protocol):
    """Optional semantic judge; deterministic grading never requires it."""

    async def judge(
        self, case: EvaluationCase, trial: TrialRecord
    ) -> dict[str, object]:
        """Score completeness, evidence, and uncertainty as structured data."""
        ...


@dataclass(frozen=True, slots=True)
class RunConfiguration:
    """Labels required to make evaluation results comparable."""

    name: str
    model: str | None
    prompt_version: str
    graph_version: str


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """In-memory result of one named configuration run."""

    trials: tuple[TrialRecord, ...]
    grades: tuple[GradeResult, ...]
    summary: dict[str, object]


async def run_suite(
    cases: tuple[EvaluationCase, ...],
    executor: EvaluationExecutor,
    configuration: RunConfiguration,
    *,
    repeats: int = 1,
) -> SuiteResult:
    """Execute and grade every selected case for one or more trials."""
    if repeats < 1:
        raise ValueError("repeats must be at least one")
    trials: list[TrialRecord] = []
    grades: list[GradeResult] = []
    for case in cases:
        for trial_index in range(repeats):
            started = time.perf_counter()
            errors: tuple[str, ...] = ()
            try:
                outcome = await executor.execute(case, trial_index=trial_index)
            except Exception as error:  # evaluation must preserve failed trials
                outcome = ExecutionOutcome(final_answer="")
                errors = (f"{type(error).__name__}: {error}",)
            elapsed = time.perf_counter() - started
            record = TrialRecord(
                schema_version=1,
                case_id=case.case_id,
                category=case.category,
                trial_id=f"{configuration.name}:{case.case_id}:{trial_index + 1}",
                configuration=configuration.name,
                model=configuration.model,
                prompt_version=configuration.prompt_version,
                graph_version=configuration.graph_version,
                tool_calls=outcome.tool_calls,
                tool_results=outcome.tool_results,
                artifacts=outcome.artifacts,
                final_answer=outcome.final_answer,
                errors=outcome.errors + errors,
                elapsed_seconds=elapsed,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=outcome.cost_usd,
            )
            trials.append(record)
            grade = grade_trial(case, record)
            if errors:
                grade = GradeResult(
                    case_id=grade.case_id,
                    trial_id=grade.trial_id,
                    passed=False,
                    metrics=grade.metrics,
                    failure_reasons=grade.failure_reasons + errors,
                    details=grade.details,
                )
            grades.append(grade)
    return SuiteResult(
        trials=tuple(trials),
        grades=tuple(grades),
        summary=summarize(cases, trials, grades),
    )
