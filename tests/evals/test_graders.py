"""Focused tests for deterministic evaluation graders."""

from evals.graders import grade_trial, normalize_arguments
from evals.schema import EvaluationCase, TrialRecord


def make_case(**overrides: object) -> EvaluationCase:
    data: dict[str, object] = {
        "schema_version": 1,
        "case_id": "quant-case",
        "category": "deterministic_quantitative",
        "question": "Calculate returns.",
        "difficulty": "medium",
        "expected_symbols": ["AAPL"],
        "expected_entities": ["Apple Inc."],
        "required_tools": ["calculate_return_statistics"],
        "optional_tools": [],
        "forbidden_tools": ["get_company_news"],
        "expected_tool_arguments": [
            {
                "tool": "calculate_return_statistics",
                "arguments": {"symbol": "AAPL", "period": "1y"},
            }
        ],
        "required_answer_elements": ["total return", "historical"],
        "numerical_expectations": [
            {
                "artifact_type": "return_statistics",
                "path": "data.summary.total_return",
                "expected": 0.12,
                "unit": "decimal",
                "absolute_tolerance": 0.001,
            }
        ],
        "fixture": "quant-case",
        "grader": {},
    }
    data.update(overrides)
    return EvaluationCase.from_dict(data)


def make_trial(**overrides: object) -> TrialRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "case_id": "quant-case",
        "category": "deterministic_quantitative",
        "trial_id": "trial-1",
        "configuration": "test",
        "model": None,
        "prompt_version": "p1",
        "graph_version": "g1",
        "tool_calls": (
            {
                "name": "calculate_return_statistics",
                "arguments": {"symbol": "aapl", "period": "1Y", "interval": "1d"},
            },
        ),
        "tool_results": (),
        "artifacts": (
            {
                "artifact_type": "return_statistics",
                "data": {
                    "summary": {"total_return": {"value": 12.0, "unit": "percent"}}
                },
            },
        ),
        "final_answer": "The historical total return was 12%.",
        "errors": (),
        "elapsed_seconds": 0.01,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
    }
    values.update(overrides)
    return TrialRecord(**values)  # type: ignore[arg-type]


def test_grades_normalized_arguments_numbers_and_answer_elements() -> None:
    result = grade_trial(make_case(), make_trial())

    assert result.passed
    assert result.metrics["tool_selection_f1"] == 1.0
    assert result.metrics["tool_argument_accuracy"] == 1.0
    assert result.metrics["numerical_accuracy"] == 1.0
    assert result.metrics["answer_element_completion"] == 1.0


def test_reports_duplicate_forbidden_and_missing_behavior() -> None:
    duplicate = {
        "name": "get_company_news",
        "arguments": {"symbol": "AAPL"},
    }
    result = grade_trial(
        make_case(),
        make_trial(
            tool_calls=(duplicate, duplicate),
            artifacts=(),
            final_answer="No evidence.",
        ),
    )

    assert not result.passed
    assert result.metrics["duplicate_tool_calls"] == 1.0
    assert result.metrics["unnecessary_tool_calls"] == 2.0
    assert any("forbidden" in reason for reason in result.failure_reasons)
    assert any("numerical" in reason for reason in result.failure_reasons)


def test_symbol_list_normalization_is_order_independent() -> None:
    assert normalize_arguments({"symbols": ["msft", "AAPL"]}) == {
        "symbols": ["AAPL", "MSFT"]
    }
