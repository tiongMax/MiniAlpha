"""Machine-readable summaries, Markdown reports, and regression checks."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from statistics import mean

from evals.schema import EvaluationCase, GradeResult, TrialRecord

LOWER_IS_BETTER = {
    "duplicate_tool_calls",
    "unnecessary_tool_calls",
    "elapsed_seconds",
    "cost_usd",
}


def summarize(
    cases: Iterable[EvaluationCase],
    trials: Iterable[TrialRecord],
    grades: Iterable[GradeResult],
) -> dict[str, object]:
    """Aggregate transparent overall and category-level metrics."""
    case_by_id = {case.case_id: case for case in cases}
    trial_list = list(trials)
    grade_list = list(grades)
    by_category: dict[str, list[GradeResult]] = defaultdict(list)
    for grade in grade_list:
        by_category[case_by_id[grade.case_id].category].append(grade)
    return {
        "schema_version": 1,
        "case_count": len(case_by_id),
        "trial_count": len(trial_list),
        "passed_trials": sum(grade.passed for grade in grade_list),
        "metrics": _average_metrics(grade_list),
        "categories": {
            category: {
                "trial_count": len(category_grades),
                "passed_trials": sum(grade.passed for grade in category_grades),
                "metrics": _average_metrics(category_grades),
            }
            for category, category_grades in sorted(by_category.items())
        },
        "failures": [
            {
                "case_id": grade.case_id,
                "trial_id": grade.trial_id,
                "reasons": list(grade.failure_reasons),
            }
            for grade in grade_list
            if not grade.passed
        ],
    }


def write_results(
    output_directory: Path,
    *,
    configuration: str,
    trials: Iterable[TrialRecord],
    grades: Iterable[GradeResult],
    summary: Mapping[str, object],
) -> tuple[Path, Path]:
    """Write raw JSON trajectories and a readable Markdown report."""
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / f"{configuration}.json"
    markdown_path = output_directory / f"{configuration}.md"
    payload = {
        "schema_version": 1,
        "configuration": configuration,
        "summary": summary,
        "trials": [trial.to_dict() for trial in trials],
        "grades": [grade.to_dict() for grade in grades],
    }
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    markdown_path.write_text(markdown_report(configuration, summary), encoding="utf-8")
    return json_path, markdown_path


def markdown_report(configuration: str, summary: Mapping[str, object]) -> str:
    """Render a concise report without hiding category failures."""
    lines = [
        f"# MiniAlpha evaluation: {configuration}",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Trials: {summary['trial_count']}",
        f"- Passed trials: {summary['passed_trials']}",
        "",
        "## Overall metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    metrics = summary.get("metrics", {})
    if isinstance(metrics, Mapping):
        for name, value in sorted(metrics.items()):
            lines.append(f"| {name} | {_format_metric(value)} |")
    lines.extend(("", "## Category results", ""))
    categories = summary.get("categories", {})
    if isinstance(categories, Mapping):
        for category, value in categories.items():
            if not isinstance(value, Mapping):
                continue
            lines.extend(
                (
                    f"### {category}",
                    "",
                    f"Trials: {value.get('trial_count', 0)}; "
                    f"passed: {value.get('passed_trials', 0)}",
                    "",
                )
            )
            category_metrics = value.get("metrics", {})
            if isinstance(category_metrics, Mapping):
                for name, metric in sorted(category_metrics.items()):
                    lines.append(f"- {name}: {_format_metric(metric)}")
                lines.append("")
    lines.extend(("## Failures", ""))
    failures = summary.get("failures", [])
    if isinstance(failures, list) and failures:
        for failure in failures:
            if isinstance(failure, Mapping):
                reasons = "; ".join(str(item) for item in failure.get("reasons", []))
                lines.append(f"- `{failure.get('case_id')}`: {reasons}")
    else:
        lines.append("No deterministic failures in this run.")
    lines.extend(
        (
            "",
            "> Frozen-reference results validate evaluation plumbing only; "
            "they are not a live-agent benchmark.",
            "",
        )
    )
    return "\n".join(lines)


def compare_summaries(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    thresholds: Mapping[str, float],
) -> list[str]:
    """Return regressions exceeding configured absolute metric deltas."""
    baseline_metrics = baseline.get("metrics", {})
    candidate_metrics = candidate.get("metrics", {})
    if not isinstance(baseline_metrics, Mapping) or not isinstance(
        candidate_metrics, Mapping
    ):
        raise ValueError("summaries must contain metrics objects")
    failures: list[str] = []
    for metric, maximum_regression in thresholds.items():
        before = baseline_metrics.get(metric)
        after = candidate_metrics.get(metric)
        if not isinstance(before, int | float) or not isinstance(after, int | float):
            continue
        regression = after - before if metric in LOWER_IS_BETTER else before - after
        if regression > maximum_regression:
            failures.append(
                f"{metric} regressed by {regression:.6f} "
                f"(allowed {maximum_regression:.6f})"
            )
    return failures


def _average_metrics(grades: Iterable[GradeResult]) -> dict[str, float | None]:
    buckets: dict[str, list[float]] = defaultdict(list)
    names: set[str] = set()
    for grade in grades:
        for name, value in grade.metrics.items():
            names.add(name)
            if value is not None:
                buckets[name].append(value)
    return {
        name: mean(buckets[name]) if buckets[name] else None for name in sorted(names)
    }


def _format_metric(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, int | float):
        return f"{value:.4f}"
    return str(value)
