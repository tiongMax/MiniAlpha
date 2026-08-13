"""Validate the locked, credential-free observability span contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT_SPAN = "mini_alpha.research_run"
REQUIRED_SPANS = {
    "research_root": ROOT_SPAN,
    "routing": "routing.decision",
    "model": "model.invoke",
    "tool": "tool.execute",
    "cache_exact": "cache.exact",
    "cache_embedding": "cache.embedding",
    "cache_semantic": "cache.semantic",
    "provider": "provider.request",
    "persistence": "persistence.finalize",
}
FORBIDDEN_ATTRIBUTE_KEYS = frozenset(
    {
        "prompt",
        "raw_prompt",
        "prompt_text",
        "query",
        "user_query",
        "symbol",
        "api_key",
        "authorization",
        "password",
        "client_secret",
        "database_url",
        "redis_url",
        "access_token",
    }
)
HIERARCHY_CODES = frozenset(
    {
        "root_count",
        "root_parent",
        "unexpected_root",
        "missing_parent",
        "unknown_parent",
        "parent_cycle",
        "disconnected_span",
        "interval_not_nested",
        "provider_parent",
    }
)


@dataclass(frozen=True, slots=True)
class SpanRecord:
    span_id: str
    parent_span_id: str | None
    name: str
    run_type: str
    status: str
    start_ms: object
    end_ms: object
    tags: tuple[str, ...]
    attributes: dict[str, object]


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    expected_root_status: str
    expected_failure_span_ids: tuple[str, ...]
    spans: tuple[SpanRecord, ...]


def _require_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _load_span(payload: object) -> SpanRecord:
    if not isinstance(payload, dict):
        raise ValueError("each span must be an object")
    parent = payload.get("parent_span_id")
    if parent is not None and not isinstance(parent, str):
        raise ValueError("parent_span_id must be a string or null")
    tags = payload.get("tags")
    attributes = payload.get("attributes")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("span tags must be a string list")
    if not isinstance(attributes, dict):
        raise ValueError("span attributes must be an object")
    return SpanRecord(
        span_id=_require_string(payload, "span_id"),
        parent_span_id=parent,
        name=_require_string(payload, "name"),
        run_type=_require_string(payload, "run_type"),
        status=_require_string(payload, "status"),
        start_ms=payload.get("start_ms"),
        end_ms=payload.get("end_ms"),
        tags=tuple(tags),
        attributes=attributes,
    )


def load_scenarios(
    path: Path,
) -> tuple[list[Scenario], str, tuple[str, ...]]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("observability corpus requires schema_version 1")
    if payload.get("contract_version") != "observability-v1":
        raise ValueError("unsupported observability contract version")
    required_names = payload.get("required_span_names")
    if required_names != list(REQUIRED_SPANS.values()):
        raise ValueError("fixture required span names do not match the evaluator")
    markers = payload.get("forbidden_markers")
    if (
        not isinstance(markers, list)
        or not markers
        or not all(isinstance(marker, str) and marker for marker in markers)
    ):
        raise ValueError("forbidden_markers must be a non-empty string list")
    scenario_payloads = payload.get("scenarios")
    if not isinstance(scenario_payloads, list):
        raise ValueError("observability corpus requires scenarios")

    scenarios: list[Scenario] = []
    for item in scenario_payloads:
        if not isinstance(item, dict):
            raise ValueError("each scenario must be an object")
        failures = item.get("expected_failure_span_ids")
        spans = item.get("spans")
        if not isinstance(failures, list) or not all(
            isinstance(failure, str) for failure in failures
        ):
            raise ValueError("expected_failure_span_ids must be a string list")
        if not isinstance(spans, list):
            raise ValueError("scenario spans must be a list")
        scenarios.append(
            Scenario(
                scenario_id=_require_string(item, "scenario_id"),
                expected_root_status=_require_string(item, "expected_root_status"),
                expected_failure_span_ids=tuple(failures),
                spans=tuple(_load_span(span) for span in spans),
            )
        )

    if len(scenarios) != 6 or len({item.scenario_id for item in scenarios}) != 6:
        raise ValueError("observability corpus requires exactly 6 unique scenarios")
    all_span_ids = [span.span_id for item in scenarios for span in item.spans]
    if len(all_span_ids) != len(set(all_span_ids)):
        raise ValueError("span IDs must be unique across the corpus")
    return scenarios, hashlib.sha256(raw).hexdigest(), tuple(markers)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _add_violation(
    violations: list[dict[str, str | None]],
    code: str,
    scenario_id: str | None,
    span_id: str | None,
    detail: str,
) -> None:
    violations.append(
        {
            "code": code,
            "scenario_id": scenario_id,
            "span_id": span_id,
            "detail": detail,
        }
    )


def _attribute_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key).casefold() for key in value}
        for child in value.values():
            keys.update(_attribute_keys(child))
        return keys
    if isinstance(value, list | tuple):
        keys: set[str] = set()
        for child in value:
            keys.update(_attribute_keys(child))
        return keys
    return set()


def _privacy_hits(span: SpanRecord, markers: tuple[str, ...]) -> list[str]:
    hits = [
        f"key:{key}"
        for key in sorted(_attribute_keys(span.attributes) & FORBIDDEN_ATTRIBUTE_KEYS)
    ]
    serialized = json.dumps(
        {"tags": span.tags, "attributes": span.attributes},
        ensure_ascii=True,
        sort_keys=True,
    ).casefold()
    hits.extend(
        f"marker:{index}"
        for index, marker in enumerate(markers)
        if marker.casefold() in serialized
    )
    return hits


def _reaches_root(
    span: SpanRecord,
    index: dict[str, SpanRecord],
    root_id: str,
) -> tuple[bool, bool]:
    current = span
    seen = {span.span_id}
    while current.parent_span_id is not None:
        if current.parent_span_id == root_id:
            return True, False
        parent = index.get(current.parent_span_id)
        if parent is None:
            return False, False
        if parent.span_id in seen:
            return False, True
        seen.add(parent.span_id)
        current = parent
    return False, False


def _required_metadata_violations(span: SpanRecord) -> list[tuple[str, str]]:
    attributes = span.attributes
    issues: list[tuple[str, str]] = []
    if not isinstance(attributes.get("outcome"), str):
        issues.append(("outcome_metadata", "outcome must be a string"))
    if span.name == "routing.decision" and not _is_nonnegative_int(
        attributes.get("selected_tool_count")
    ):
        issues.append(
            (
                "routing_metadata",
                "selected_tool_count must be a non-negative integer",
            )
        )
    if span.name in {"model.invoke", "tool.execute", "provider.request"}:
        attempt = attributes.get("attempt")
        if not _is_nonnegative_int(attempt) or attempt == 0:
            issues.append(("attempt_metadata", "attempt must be a positive integer"))
    if span.name in {"cache.exact", "cache.embedding", "cache.semantic"}:
        if not isinstance(attributes.get("cache_status"), str) or not isinstance(
            attributes.get("cache_tier"), str
        ):
            issues.append(
                (
                    "cache_metadata",
                    "cache_status and cache_tier must be strings",
                )
            )
    if span.name == "provider.request" and not isinstance(
        attributes.get("provider_operation"), str
    ):
        issues.append(("provider_metadata", "provider_operation must be a string"))
    if span.name == "persistence.finalize" and not isinstance(
        attributes.get("persistence_operation"), str
    ):
        issues.append(
            ("persistence_metadata", "persistence_operation must be a string")
        )
    return issues


def _model_usage_valid(span: SpanRecord) -> bool:
    attributes = span.attributes
    values = [
        attributes.get("input_tokens"),
        attributes.get("output_tokens"),
        attributes.get("total_tokens"),
    ]
    return all(_is_nonnegative_int(value) for value in values) and (
        values[0] + values[1] == values[2]
    )


def build_report(
    scenarios: list[Scenario],
    digest: str,
    forbidden_markers: tuple[str, ...],
) -> dict[str, object]:
    violations: list[dict[str, str | None]] = []
    coverage = Counter(span.name for scenario in scenarios for span in scenario.spans)
    scenario_coverage = {
        name: len(
            {
                scenario.scenario_id
                for scenario in scenarios
                if any(span.name == name for span in scenario.spans)
            }
        )
        for name in REQUIRED_SPANS.values()
    }
    all_spans = [span for scenario in scenarios for span in scenario.spans]
    latency_by_name: Counter[str] = Counter()
    numeric_latency_spans = 0
    model_spans = [span for span in all_spans if span.name == "model.invoke"]
    model_spans_with_usage = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    expected_failure_count = 0
    observed_failure_count = 0
    attributed_failure_count = 0
    total_parent_links = 0
    valid_parent_links = 0
    provider_tool_parent_links = 0
    rooted_scenarios = 0
    privacy_hit_count = 0

    for category, name in REQUIRED_SPANS.items():
        if coverage[name] == 0:
            _add_violation(
                violations,
                "missing_required_span",
                None,
                None,
                f"required category {category} has no {name} span",
            )

    for scenario in scenarios:
        before_scenario = len(violations)
        index = {span.span_id: span for span in scenario.spans}
        roots = [span for span in scenario.spans if span.name == ROOT_SPAN]
        root = roots[0] if len(roots) == 1 else None
        if len(roots) != 1:
            _add_violation(
                violations,
                "root_count",
                scenario.scenario_id,
                None,
                "scenario must contain exactly one research root",
            )
        elif root is not None:
            if root.parent_span_id is not None:
                _add_violation(
                    violations,
                    "root_parent",
                    scenario.scenario_id,
                    root.span_id,
                    "research root must not have a parent",
                )
            if root.status != scenario.expected_root_status:
                _add_violation(
                    violations,
                    "root_status",
                    scenario.scenario_id,
                    root.span_id,
                    "root status does not match the locked expectation",
                )

        expected_failures = set(scenario.expected_failure_span_ids)
        observed_failures = {
            span.span_id for span in scenario.spans if span.status == "error"
        }
        expected_failure_count += len(expected_failures)
        observed_failure_count += len(observed_failures)
        attributed_failure_count += len(expected_failures & observed_failures)
        if expected_failures != observed_failures:
            _add_violation(
                violations,
                "failure_set",
                scenario.scenario_id,
                None,
                "observed error spans do not match locked failure attribution",
            )

        for span in scenario.spans:
            if span.status not in {"ok", "error"}:
                _add_violation(
                    violations,
                    "span_status",
                    scenario.scenario_id,
                    span.span_id,
                    "status must be ok or error",
                )
            if span.name != ROOT_SPAN and span.parent_span_id is None:
                _add_violation(
                    violations,
                    "missing_parent",
                    scenario.scenario_id,
                    span.span_id,
                    "non-root span must have a parent",
                )
            if span.name != ROOT_SPAN and span.parent_span_id is not None:
                total_parent_links += 1
                parent = index.get(span.parent_span_id)
                reaches_root = False
                has_cycle = False
                interval_nested = False
                if parent is None:
                    _add_violation(
                        violations,
                        "unknown_parent",
                        scenario.scenario_id,
                        span.span_id,
                        "parent span does not exist in the same scenario",
                    )
                elif root is not None:
                    reaches_root, has_cycle = _reaches_root(span, index, root.span_id)
                    if has_cycle:
                        _add_violation(
                            violations,
                            "parent_cycle",
                            scenario.scenario_id,
                            span.span_id,
                            "parent chain contains a cycle",
                        )
                    elif not reaches_root:
                        _add_violation(
                            violations,
                            "disconnected_span",
                            scenario.scenario_id,
                            span.span_id,
                            "span is not connected to the research root",
                        )
                    if all(
                        _is_number(value)
                        for value in (
                            parent.start_ms,
                            parent.end_ms,
                            span.start_ms,
                            span.end_ms,
                        )
                    ):
                        interval_nested = (
                            parent.start_ms <= span.start_ms
                            and span.end_ms <= parent.end_ms
                        )
                        if not interval_nested:
                            _add_violation(
                                violations,
                                "interval_not_nested",
                                scenario.scenario_id,
                                span.span_id,
                                "child interval must be contained by its parent",
                            )
                    if reaches_root and not has_cycle and interval_nested:
                        valid_parent_links += 1
                if span.name == "provider.request" and parent is not None:
                    if parent.name == "tool.execute":
                        provider_tool_parent_links += 1
                    else:
                        _add_violation(
                            violations,
                            "provider_parent",
                            scenario.scenario_id,
                            span.span_id,
                            "provider request must be attributed to a tool span",
                        )

            if span.name != ROOT_SPAN and span.parent_span_id is None:
                pass
            elif span.name == ROOT_SPAN and span is not root:
                _add_violation(
                    violations,
                    "unexpected_root",
                    scenario.scenario_id,
                    span.span_id,
                    "additional research root is not allowed",
                )

            duration = span.attributes.get("duration_ms")
            if (
                not _is_number(span.start_ms)
                or not _is_number(span.end_ms)
                or not _is_number(duration)
                or span.end_ms < span.start_ms
                or duration < 0
                or not math.isclose(
                    duration,
                    span.end_ms - span.start_ms,
                    abs_tol=0.001,
                )
            ):
                _add_violation(
                    violations,
                    "latency_attribution",
                    scenario.scenario_id,
                    span.span_id,
                    "duration_ms must be numeric and match the span interval",
                )
            else:
                numeric_latency_spans += 1
                latency_by_name[span.name] += duration

            for code, detail in _required_metadata_violations(span):
                _add_violation(
                    violations,
                    code,
                    scenario.scenario_id,
                    span.span_id,
                    detail,
                )

            if span.name == "model.invoke":
                if _model_usage_valid(span):
                    model_spans_with_usage += 1
                    input_tokens += int(span.attributes["input_tokens"])
                    output_tokens += int(span.attributes["output_tokens"])
                    total_tokens += int(span.attributes["total_tokens"])
                else:
                    _add_violation(
                        violations,
                        "token_attribution",
                        scenario.scenario_id,
                        span.span_id,
                        "model token counts must be numeric, non-negative, "
                        "and additive",
                    )

            if span.status == "error":
                if span.attributes.get("outcome") != "error" or not isinstance(
                    span.attributes.get("error_type"), str
                ):
                    _add_violation(
                        violations,
                        "failure_attribution",
                        scenario.scenario_id,
                        span.span_id,
                        "error spans need outcome=error and a safe error_type",
                    )

            hits = _privacy_hits(span, forbidden_markers)
            privacy_hit_count += len(hits)
            if hits:
                _add_violation(
                    violations,
                    "privacy",
                    scenario.scenario_id,
                    span.span_id,
                    "span contains a forbidden key or marker",
                )

        scenario_violations = violations[before_scenario:]
        if not any(item["code"] in HIERARCHY_CODES for item in scenario_violations):
            rooted_scenarios += 1

    violation_counts = Counter(str(item["code"]) for item in violations)
    coverage_report = {
        category: {
            "span_name": name,
            "span_count": coverage[name],
            "scenario_count": scenario_coverage[name],
        }
        for category, name in REQUIRED_SPANS.items()
    }
    return {
        "schema_version": 1,
        "contract_version": "observability-v1",
        "evaluation_type": "credential-free synthetic span-contract validation",
        "dataset_sha256": digest,
        "scenario_count": len(scenarios),
        "span_count": len(all_spans),
        "required_span_coverage": coverage_report,
        "parent_child_attribution": {
            "rooted_scenarios": rooted_scenarios,
            "total_scenarios": len(scenarios),
            "valid_parent_links": valid_parent_links,
            "total_parent_links": total_parent_links,
            "provider_tool_parent_links": provider_tool_parent_links,
        },
        "latency_attribution": {
            "spans_with_numeric_duration": numeric_latency_spans,
            "total_spans": len(all_spans),
            "duration_ms_by_span_name": {
                name: latency_by_name[name] for name in REQUIRED_SPANS.values()
            },
        },
        "token_attribution": {
            "model_spans_with_numeric_usage": model_spans_with_usage,
            "total_model_spans": len(model_spans),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        },
        "failure_attribution": {
            "expected_error_spans": expected_failure_count,
            "observed_error_spans": observed_failure_count,
            "correctly_attributed_error_spans": attributed_failure_count,
        },
        "privacy_scan": {
            "scanned_spans": len(all_spans),
            "forbidden_marker_count": len(forbidden_markers),
            "privacy_hits": privacy_hit_count,
        },
        "violation_counts": dict(sorted(violation_counts.items())),
        "violations": violations,
        "passed": not violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path("evals/observability/scenarios_v1.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/results/observability_v1.json"),
    )
    args = parser.parse_args()
    scenarios, digest, markers = load_scenarios(args.scenarios)
    report = build_report(scenarios, digest, markers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
