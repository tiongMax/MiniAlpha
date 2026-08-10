"""Strict JSON loading for cases and frozen outcomes."""

from __future__ import annotations

import json
from pathlib import Path

from evals.schema import EvaluationCase, EvaluationSchemaError, ExecutionOutcome


def load_cases(path: Path) -> tuple[EvaluationCase, ...]:
    """Load unique evaluation cases from a versioned JSON document."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise EvaluationSchemaError("case collection requires schema_version 1")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list):
        raise EvaluationSchemaError("case collection requires a cases list")
    cases = tuple(EvaluationCase.from_dict(item) for item in raw_cases)
    identifiers = [case.case_id for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise EvaluationSchemaError("case IDs must be unique")
    return cases


def load_frozen_outcomes(path: Path) -> dict[str, ExecutionOutcome]:
    """Load credential-free executor outputs keyed by fixture reference."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise EvaluationSchemaError("fixture collection requires schema_version 1")
    raw_fixtures = data.get("fixtures")
    if not isinstance(raw_fixtures, dict):
        raise EvaluationSchemaError("fixture collection requires a fixtures object")
    outcomes: dict[str, ExecutionOutcome] = {}
    for fixture_id, raw in raw_fixtures.items():
        if not isinstance(fixture_id, str) or not isinstance(raw, dict):
            raise EvaluationSchemaError("fixture entries must be named objects")
        outcomes[fixture_id] = ExecutionOutcome(
            final_answer=str(raw.get("final_answer", "")),
            tool_calls=_object_tuple(raw.get("tool_calls", []), "tool_calls"),
            tool_results=_object_tuple(raw.get("tool_results", []), "tool_results"),
            artifacts=_object_tuple(raw.get("artifacts", []), "artifacts"),
            errors=_string_tuple(raw.get("errors", []), "errors"),
            input_tokens=_optional_int(raw.get("input_tokens"), "input_tokens"),
            output_tokens=_optional_int(raw.get("output_tokens"), "output_tokens"),
            cost_usd=_optional_float(raw.get("cost_usd"), "cost_usd"),
        )
    return outcomes


def _object_tuple(value: object, name: str) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise EvaluationSchemaError(f"{name} must be a list of objects")
    return tuple(value)


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvaluationSchemaError(f"{name} must be a list of strings")
    return tuple(value)


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationSchemaError(f"{name} must be an integer or null")
    return value


def _optional_float(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EvaluationSchemaError(f"{name} must be a number or null")
    return float(value)
