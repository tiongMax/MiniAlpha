"""Tests for repeatable execution, reporting, and regression thresholds."""

import asyncio
import json
from pathlib import Path

from evals.frozen import FrozenExecutor
from evals.loader import load_cases, load_frozen_outcomes
from evals.reporting import compare_summaries, write_results
from evals.runner import RunConfiguration, run_suite

ROOT = Path(__file__).resolve().parents[2]


def test_runner_captures_repeated_trials_and_nullable_usage(tmp_path: Path) -> None:
    cases = load_cases(ROOT / "evals" / "cases" / "v1.json")[:2]
    executor = FrozenExecutor(
        load_frozen_outcomes(ROOT / "evals" / "fixtures" / "v1.json")
    )
    configuration = RunConfiguration(
        name="fixture-test",
        model=None,
        prompt_version="frozen-v1",
        graph_version="frozen-v1",
    )

    result = asyncio.run(run_suite(cases, executor, configuration, repeats=2))

    assert len(result.trials) == 4
    assert len({trial.trial_id for trial in result.trials}) == 4
    assert all(trial.input_tokens is None for trial in result.trials)
    assert result.summary["trial_count"] == 4
    assert result.summary["case_count"] == 2
    assert all(grade.passed for grade in result.grades)

    json_path, markdown_path = write_results(
        tmp_path,
        configuration=configuration.name,
        trials=result.trials,
        grades=result.grades,
        summary=result.summary,
    )
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    report = markdown_path.read_text(encoding="utf-8")
    assert len(payload["trials"]) == 4
    assert "Category results" in report
    assert "not a live-agent benchmark" in report


def test_regression_thresholds_obey_metric_direction() -> None:
    baseline = {
        "metrics": {
            "tool_selection_f1": 0.9,
            "duplicate_tool_calls": 0.0,
        }
    }
    candidate = {
        "metrics": {
            "tool_selection_f1": 0.85,
            "duplicate_tool_calls": 1.0,
        }
    }

    failures = compare_summaries(
        baseline,
        candidate,
        {"tool_selection_f1": 0.02, "duplicate_tool_calls": 0.0},
    )

    assert len(failures) == 2
    assert any("tool_selection_f1" in failure for failure in failures)
    assert any("duplicate_tool_calls" in failure for failure in failures)
