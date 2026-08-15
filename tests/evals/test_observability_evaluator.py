"""Contracts for the locked, credential-free observability evaluator."""

from dataclasses import replace
from pathlib import Path

from scripts.evaluate_observability import (
    REQUIRED_SPANS,
    build_report,
    load_scenarios,
)

SCENARIO_PATH = Path("evals/observability/scenarios_v1.json")


def test_observability_corpus_locks_six_unique_scenarios() -> None:
    scenarios, digest, markers = load_scenarios(SCENARIO_PATH)

    assert len(scenarios) == 6
    assert len({scenario.scenario_id for scenario in scenarios}) == 6
    assert len(digest) == 64
    assert markers
    assert any(scenario.expected_failure_span_ids for scenario in scenarios)


def test_observability_report_covers_and_attributes_the_contract() -> None:
    scenarios, digest, markers = load_scenarios(SCENARIO_PATH)
    report = build_report(scenarios, digest, markers)

    assert report["passed"] is True
    coverage = report["required_span_coverage"]
    assert set(coverage) == set(REQUIRED_SPANS)
    assert all(item["span_count"] > 0 for item in coverage.values())

    parents = report["parent_child_attribution"]
    assert parents["rooted_scenarios"] == parents["total_scenarios"] == 6
    assert parents["valid_parent_links"] == parents["total_parent_links"]
    assert parents["provider_tool_parent_links"] == 2

    latency = report["latency_attribution"]
    assert latency["spans_with_numeric_duration"] == latency["total_spans"]
    tokens = report["token_attribution"]
    assert tokens["model_spans_with_numeric_usage"] == tokens["total_model_spans"]
    assert tokens["input_tokens"] + tokens["output_tokens"] == tokens["total_tokens"]

    failures = report["failure_attribution"]
    assert failures["expected_error_spans"] > 0
    assert (
        failures["correctly_attributed_error_spans"]
        == failures["expected_error_spans"]
        == failures["observed_error_spans"]
    )
    assert report["privacy_scan"]["privacy_hits"] == 0


def test_observability_report_rejects_sensitive_attributes() -> None:
    scenarios, digest, markers = load_scenarios(SCENARIO_PATH)
    first = scenarios[0]
    leaked_span = replace(
        first.spans[0],
        attributes={**first.spans[0].attributes, "raw_prompt": "private text"},
    )
    tampered = [replace(first, spans=(leaked_span, *first.spans[1:])), *scenarios[1:]]

    report = build_report(tampered, digest, markers)

    assert report["passed"] is False
    assert report["privacy_scan"]["privacy_hits"] > 0
    assert report["violation_counts"]["privacy"] == 1


def test_observability_report_rejects_broken_parent_attribution() -> None:
    scenarios, digest, markers = load_scenarios(SCENARIO_PATH)
    first = scenarios[0]
    orphan = replace(first.spans[1], parent_span_id="missing-parent")
    tampered = [
        replace(first, spans=(first.spans[0], orphan, *first.spans[2:])),
        *scenarios[1:],
    ]

    report = build_report(tampered, digest, markers)

    assert report["passed"] is False
    assert report["violation_counts"]["unknown_parent"] == 1
    assert report["parent_child_attribution"]["rooted_scenarios"] == 5


def test_observability_report_rejects_missing_required_span_category() -> None:
    scenarios, digest, markers = load_scenarios(SCENARIO_PATH)
    tampered = []
    for scenario in scenarios:
        spans = tuple(
            span for span in scenario.spans if span.name != "provider.request"
        )
        remaining_ids = {span.span_id for span in spans}
        failures = tuple(
            span_id
            for span_id in scenario.expected_failure_span_ids
            if span_id in remaining_ids
        )
        tampered.append(
            replace(
                scenario,
                spans=spans,
                expected_failure_span_ids=failures,
            )
        )

    report = build_report(tampered, digest, markers)

    assert report["passed"] is False
    assert report["required_span_coverage"]["provider"]["span_count"] == 0
    assert report["violation_counts"]["missing_required_span"] == 1


def test_observability_report_rejects_bad_numeric_and_failure_attribution() -> None:
    scenarios, digest, markers = load_scenarios(SCENARIO_PATH)
    target = next(
        scenario
        for scenario in scenarios
        if scenario.scenario_id == "model_retry_then_success"
    )
    failed_model = target.spans[2]
    successful_model = target.spans[3]
    bad_failure = replace(failed_model, status="ok")
    bad_numbers = replace(
        successful_model,
        attributes={
            **successful_model.attributes,
            "duration_ms": 51,
            "total_tokens": 171,
        },
    )
    bad_target = replace(
        target,
        spans=(*target.spans[:2], bad_failure, bad_numbers, *target.spans[4:]),
    )
    tampered = [
        bad_target if scenario.scenario_id == target.scenario_id else scenario
        for scenario in scenarios
    ]

    report = build_report(tampered, digest, markers)

    assert report["passed"] is False
    assert report["violation_counts"]["failure_set"] == 1
    assert report["violation_counts"]["latency_attribution"] == 1
    assert report["violation_counts"]["token_attribution"] == 1
