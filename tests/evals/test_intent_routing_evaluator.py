"""Deterministic contracts for the paired routing evaluator."""

import json
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage

from scripts.evaluate_intent_routing import (
    ObservedCall,
    RoutingCase,
    RoutingTrial,
    build_report,
    grade_calls,
    load_cases,
)


def test_locked_routing_corpus_has_100_unique_queries() -> None:
    cases, digest = load_cases(Path("evals/routing/cases_v1.json"))

    assert len(cases) == 100
    assert len(digest) == 64
    assert {case.category for case in cases} == {
        "single_intent",
        "multi_intent",
        "no_tool",
        "ambiguous_fallback",
    }


def test_grade_calls_detects_missing_unnecessary_and_duplicate_calls() -> None:
    case = RoutingCase(
        case_id="sample",
        category="test",
        prompt="sample",
        required_tools=("get_company_news", "get_company_overview"),
        optional_tools=(),
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"id": "1", "name": "get_company_news", "args": {"symbol": "AAPL"}},
                {"id": "2", "name": "get_company_news", "args": {"symbol": "AAPL"}},
                {"id": "3", "name": "calculate_volatility", "args": {"symbol": "AAPL"}},
            ],
        ),
        ToolMessage(content="news", tool_call_id="1", name="get_company_news"),
        ToolMessage(content="news", tool_call_id="2", name="get_company_news"),
        ToolMessage(content="risk", tool_call_id="3", name="calculate_volatility"),
    ]

    _calls, missing, unnecessary, duplicates, error = grade_calls(messages, case)

    assert missing == ("get_company_overview",)
    assert unnecessary == ("calculate_volatility",)
    assert duplicates == 1
    assert error is True


def test_report_computes_paired_error_and_schema_changes() -> None:
    cases = [RoutingCase("one", "test", "prompt", (), ())]

    def trial(variant, *, selection_error, selected):
        return RoutingTrial(
            variant=variant,
            case_id="one",
            repeat=1,
            completed=True,
            attempts=1,
            duration_ms=10.0,
            error_type=None,
            routing_mode=None,
            selected_tool_names=tuple(str(index) for index in range(selected)),
            calls=(ObservedCall("tool", {}, True),),
            missing_required_tools=(),
            unnecessary_tools=(),
            duplicate_tool_calls=0,
            selection_error=selection_error,
        )

    report = build_report(
        [
            trial("fixed_16", selection_error=True, selected=16),
            trial("intent_routed", selection_error=False, selected=4),
        ],
        cases=cases,
        dataset_sha256="a" * 64,
        repeats=1,
    )

    assert report["selection_error_change_percentage_points"] == -100.0
    assert report["relative_selected_schema_reduction"] == 0.75


def test_routing_corpus_is_valid_json() -> None:
    payload = json.loads(
        Path("evals/routing/cases_v1.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 1
