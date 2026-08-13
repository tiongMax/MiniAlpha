"""Contracts for the locked cache evaluator."""

import asyncio
from pathlib import Path

from scripts.evaluate_cache import build_report, load_cases, run


def test_cache_corpus_and_expected_outcomes_are_locked() -> None:
    cases, digest = load_cases(Path("evals/cache/cases_v1.json"))

    assert len(cases) == 13
    assert len(digest) == 64
    assert {case.kind for case in cases} >= {
        "cold",
        "exact_repeat",
        "semantic_paraphrase",
        "adversarial_near_miss",
        "error_not_cached",
    }


def test_cache_evaluator_reports_no_false_semantic_hits() -> None:
    cases, digest = load_cases(Path("evals/cache/cases_v1.json"))
    report = build_report(asyncio.run(run(cases)), digest)

    assert report["false_semantic_hits"] == 0
    assert report["cache_enabled"]["correct_outcomes"] == len(cases)
    assert report["generation_token_reduction"] > 0
    assert report["warm_exact_p50_ms"] < report["cold_miss_p50_ms"]
