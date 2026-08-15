"""Contracts for the locked 50-scenario recovery evaluator."""

from pathlib import Path

from scripts.evaluate_failure_recovery import build_report, load_cases


def test_failure_corpus_has_exactly_50_unique_scheduled_cases() -> None:
    cases, digest = load_cases(Path("evals/failures/cases_v1.json"))

    assert len(cases) == 50
    assert len(digest) == 64
    assert {case.category for case in cases} == {
        "provider",
        "tool_input",
        "cache",
        "model",
        "events",
        "persistence",
        "worker",
    }


def test_recovery_report_never_excludes_terminal_cases() -> None:
    cases, digest = load_cases(Path("evals/failures/cases_v1.json"))
    report = build_report(cases, digest)

    assert report["baseline"]["scheduled_requests"] == 50
    assert report["recovery_enabled"]["scheduled_requests"] == 50
    assert report["recovery_enabled"]["policy_correct_rate"] == 1.0
    assert report["recovery_enabled"]["usable_completion_rate"] < 1.0
    assert report["secret_leak_count"] == 0
