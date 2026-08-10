"""Tests for versioned case and fixture loading."""

from pathlib import Path

import pytest

from evals.loader import load_cases, load_frozen_outcomes
from evals.schema import EvaluationCase, EvaluationSchemaError

ROOT = Path(__file__).resolve().parents[2]


def test_initial_suite_has_requested_coverage_and_fixtures() -> None:
    cases = load_cases(ROOT / "evals" / "cases" / "v1.json")
    fixtures = load_frozen_outcomes(ROOT / "evals" / "fixtures" / "v1.json")

    assert len(cases) == 20
    assert {case.category for case in cases} == {
        "company_fundamental_retrieval",
        "deterministic_quantitative",
        "multi_company_comparison",
        "multi_step_research",
        "missing_invalid_data",
        "multi_turn_followup",
    }
    assert sum(bool(case.turns) for case in cases) >= 3
    assert {case.fixture for case in cases}.issubset(fixtures)
    assert all(case.required_tools for case in cases)
    assert all(case.required_answer_elements for case in cases)


def test_rejects_unsupported_case_schema() -> None:
    with pytest.raises(EvaluationSchemaError, match="unsupported"):
        EvaluationCase.from_dict(
            {
                "schema_version": 2,
                "case_id": "future",
                "category": "test",
                "question": "test",
                "difficulty": "easy",
                "fixture": "future",
            }
        )
