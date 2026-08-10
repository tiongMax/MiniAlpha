"""Credential-free deterministic graders for financial-agent trials."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence

from evals.schema import EvaluationCase, GradeResult, NumericExpectation, TrialRecord


def normalize_arguments(value: object, *, key: str | None = None) -> object:
    """Normalize tool arguments while preserving meaningful semantics."""
    if isinstance(value, Mapping):
        return {
            str(item_key): normalize_arguments(item_value, key=str(item_key))
            for item_key, item_value in sorted(
                value.items(), key=lambda item: str(item[0])
            )
        }
    if isinstance(value, list | tuple):
        normalized = [normalize_arguments(item, key=key) for item in value]
        if key in {"symbols", "entities"}:
            return sorted(normalized, key=str)
        return normalized
    if isinstance(value, str):
        stripped = value.strip()
        if key in {"symbol", "symbols"}:
            return stripped.upper().replace(".", "-")
        return (
            stripped.lower() if key in {"interval", "period", "frequency"} else stripped
        )
    return value


def normalized_call_key(call: Mapping[str, object]) -> str:
    """Return a stable key used to identify duplicate calls."""
    name = str(call.get("name", ""))
    arguments = normalize_arguments(call.get("arguments", {}))
    return f"{name}:{json.dumps(arguments, sort_keys=True, separators=(',', ':'))}"


def grade_trial(case: EvaluationCase, trial: TrialRecord) -> GradeResult:
    """Run all deterministic graders and return transparent failure reasons."""
    names = [str(call.get("name", "")) for call in trial.tool_calls]
    required = set(case.required_tools)
    optional = set(case.optional_tools)
    allowed = required | optional
    present_required = required.intersection(names)
    relevant_calls = sum(name in allowed for name in names)
    precision = relevant_calls / len(names) if names else (1.0 if not required else 0.0)
    recall = len(present_required) / len(required) if required else 1.0
    f1 = _f1(precision, recall)

    argument_scores: list[float] = []
    for expectation in case.expected_tool_arguments:
        candidates = [
            call for call in trial.tool_calls if call.get("name") == expectation.tool
        ]
        matched = any(
            _arguments_match(
                expectation.arguments,
                call.get("arguments", {}),
                subset=case.grader.argument_subset_match,
            )
            for call in candidates
        )
        argument_scores.append(float(matched))
    argument_accuracy = (
        sum(argument_scores) / len(argument_scores) if argument_scores else 1.0
    )

    numeric_scores: list[float] = []
    numeric_details: list[dict[str, object]] = []
    for expectation in case.numerical_expectations:
        detail = _grade_number(expectation, trial.artifacts)
        numeric_details.append(detail)
        numeric_scores.append(float(bool(detail["passed"])))
    numerical_accuracy = (
        sum(numeric_scores) / len(numeric_scores) if numeric_scores else 1.0
    )

    normalized_answer = trial.final_answer.casefold()
    missing_elements = [
        element
        for element in case.required_answer_elements
        if element.casefold() not in normalized_answer
    ]
    total_elements = len(case.required_answer_elements)
    answer_completion = (
        (total_elements - len(missing_elements)) / total_elements
        if total_elements
        else 1.0
    )

    call_counts = Counter(normalized_call_key(call) for call in trial.tool_calls)
    duplicate_calls = sum(count - 1 for count in call_counts.values() if count > 1)
    unnecessary_calls = (
        sum(name not in allowed for name in names) if allowed else len(names)
    )
    forbidden_calls = [name for name in names if name in case.forbidden_tools]

    metrics: dict[str, float | None] = {
        "tool_selection_precision": precision,
        "tool_selection_recall": recall,
        "tool_selection_f1": f1,
        "tool_argument_accuracy": argument_accuracy,
        "numerical_accuracy": numerical_accuracy,
        "answer_element_completion": answer_completion,
        "duplicate_tool_calls": float(duplicate_calls),
        "unnecessary_tool_calls": float(unnecessary_calls),
        "elapsed_seconds": trial.elapsed_seconds,
        "input_tokens": float(trial.input_tokens)
        if trial.input_tokens is not None
        else None,
        "output_tokens": (
            float(trial.output_tokens) if trial.output_tokens is not None else None
        ),
        "cost_usd": trial.cost_usd,
    }

    reasons: list[str] = []
    missing_tools = sorted(required.difference(names))
    if missing_tools:
        reasons.append(f"missing required tools: {', '.join(missing_tools)}")
    if unnecessary_calls:
        reasons.append(f"{unnecessary_calls} unnecessary tool call(s)")
    if forbidden_calls:
        reasons.append(f"forbidden tools called: {', '.join(sorted(forbidden_calls))}")
    if argument_accuracy < 1:
        reasons.append("one or more tool arguments did not match")
    if numerical_accuracy < 1:
        reasons.append("one or more numerical expectations failed")
    if missing_elements and case.grader.require_all_answer_elements:
        reasons.append(f"missing answer elements: {', '.join(missing_elements)}")
    if duplicate_calls and case.grader.forbid_duplicate_calls:
        reasons.append(f"{duplicate_calls} duplicate tool call(s)")

    return GradeResult(
        case_id=case.case_id,
        trial_id=trial.trial_id,
        passed=not reasons,
        metrics=metrics,
        failure_reasons=tuple(reasons),
        details={
            "missing_answer_elements": missing_elements,
            "numeric_expectations": numeric_details,
            "forbidden_tools": forbidden_calls,
        },
    )


def _arguments_match(expected: object, observed: object, *, subset: bool) -> bool:
    normalized_expected = normalize_arguments(expected)
    normalized_observed = normalize_arguments(observed)
    if not isinstance(normalized_expected, dict) or not isinstance(
        normalized_observed, dict
    ):
        return normalized_expected == normalized_observed
    if not subset:
        return normalized_expected == normalized_observed
    return all(
        key in normalized_observed and normalized_observed[key] == value
        for key, value in normalized_expected.items()
    )


def _grade_number(
    expectation: NumericExpectation,
    artifacts: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    candidates = [
        artifact
        for artifact in artifacts
        if artifact.get("artifact_type") == expectation.artifact_type
    ]
    if not candidates:
        return {
            "artifact_type": expectation.artifact_type,
            "path": expectation.path,
            "passed": False,
            "reason": "artifact not found",
        }
    observed: object | None = None
    for artifact in candidates:
        try:
            observed = _resolve_path(artifact, expectation.path)
            break
        except KeyError:
            continue
    if observed is None:
        return {
            "artifact_type": expectation.artifact_type,
            "path": expectation.path,
            "passed": False,
            "reason": "numeric path not found",
        }
    try:
        observed_value, observed_unit = _numeric_value(observed, expectation.unit)
        actual = _canonical_number(observed_value, observed_unit)
        expected = _canonical_number(expectation.expected, expectation.unit)
    except (TypeError, ValueError) as error:
        return {
            "artifact_type": expectation.artifact_type,
            "path": expectation.path,
            "passed": False,
            "reason": str(error),
            "observed": observed,
        }
    difference = abs(actual - expected)
    allowed = max(
        _canonical_tolerance(expectation.absolute_tolerance, expectation.unit),
        abs(expected) * expectation.relative_tolerance,
    )
    return {
        "artifact_type": expectation.artifact_type,
        "path": expectation.path,
        "expected_canonical": expected,
        "observed_canonical": actual,
        "allowed_difference": allowed,
        "passed": difference <= allowed,
    }


def _resolve_path(data: Mapping[str, object], path: str) -> object:
    current: object = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            current = current[int(part)]
            continue
        raise KeyError(path)
    return current


def _numeric_value(value: object, default_unit: str) -> tuple[float, str]:
    unit = default_unit
    raw = value
    if isinstance(value, Mapping):
        raw = value.get("value")
        unit = str(value.get("unit", default_unit))
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise TypeError("observed value is not numeric")
    return float(raw), unit


def _canonical_number(value: float, unit: str) -> float:
    factors = {
        "number": 1.0,
        "decimal": 1.0,
        "percent": 0.01,
        "percentage_point": 0.01,
        "basis_point": 0.0001,
        "thousand": 1_000.0,
        "million": 1_000_000.0,
        "billion": 1_000_000_000.0,
    }
    try:
        return value * factors[unit]
    except KeyError as error:
        raise ValueError(f"unsupported unit: {unit}") from error


def _canonical_tolerance(value: float, unit: str) -> float:
    return abs(_canonical_number(value, unit))


def _f1(precision: float, recall: float) -> float:
    if math.isclose(precision + recall, 0.0):
        return 0.0
    return 2 * precision * recall / (precision + recall)
