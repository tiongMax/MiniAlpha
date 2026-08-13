"""Run paired fixed-16 and intent-routed trials on a locked query corpus."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.graph import build_graph
from app.config import create_model
from evals.graders import normalized_call_key
from evals.synthetic_finance import create_synthetic_financial_tools

Variant = Literal["fixed_16", "intent_routed"]


@dataclass(frozen=True, slots=True)
class RoutingCase:
    """One independently labeled query in the locked routing corpus."""

    case_id: str
    category: str
    prompt: str
    required_tools: tuple[str, ...]
    optional_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservedCall:
    """One model-proposed tool call and its execution outcome."""

    name: str
    arguments: dict[str, object]
    successful: bool


@dataclass(frozen=True, slots=True)
class RoutingTrial:
    """Auditable trajectory and deterministic selection grade for one trial."""

    variant: Variant
    case_id: str
    repeat: int
    completed: bool
    attempts: int
    duration_ms: float
    error_type: str | None
    routing_mode: str | None
    selected_tool_names: tuple[str, ...]
    calls: tuple[ObservedCall, ...]
    missing_required_tools: tuple[str, ...]
    unnecessary_tools: tuple[str, ...]
    duplicate_tool_calls: int
    selection_error: bool


def load_cases(path: Path) -> tuple[list[RoutingCase], str]:
    """Validate the immutable labeled query set and return its SHA-256."""
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("routing corpus requires schema_version 1")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("routing corpus requires a cases list")
    cases: list[RoutingCase] = []
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError("routing cases must be objects")
        required = item.get("required_tools", [])
        optional = item.get("optional_tools", [])
        if not isinstance(required, list) or not isinstance(optional, list):
            raise ValueError("routing labels must be tool-name lists")
        cases.append(
            RoutingCase(
                case_id=str(item["case_id"]),
                category=str(item["category"]),
                prompt=str(item["prompt"]),
                required_tools=tuple(str(name) for name in required),
                optional_tools=tuple(str(name) for name in optional),
            )
        )
    identifiers = [case.case_id for case in cases]
    prompts = [case.prompt for case in cases]
    if len(identifiers) != len(set(identifiers)) or len(prompts) != len(set(prompts)):
        raise ValueError("routing cases require unique IDs and prompts")
    return cases, hashlib.sha256(raw).hexdigest()


def grade_calls(
    messages: list[object],
    case: RoutingCase,
) -> tuple[tuple[ObservedCall, ...], tuple[str, ...], tuple[str, ...], int, bool]:
    """Grade semantic tool selection independently of answer prose."""
    results = {
        message.tool_call_id: message
        for message in messages
        if isinstance(message, ToolMessage)
    }
    calls: list[ObservedCall] = []
    raw_keys: list[str] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            raw_arguments = call.get("args", {})
            arguments = (
                cast(dict[str, object], raw_arguments)
                if isinstance(raw_arguments, dict)
                else {}
            )
            call_id = str(call.get("id") or "")
            result = results.get(call_id)
            successful = (
                result is not None and getattr(result, "status", None) != "error"
            )
            if successful and isinstance(result.artifact, dict):
                successful = result.artifact.get("status") != "error"
            observed = ObservedCall(
                name=str(call.get("name", "")),
                arguments=arguments,
                successful=successful,
            )
            calls.append(observed)
            raw_keys.append(
                normalized_call_key(
                    {"name": observed.name, "arguments": observed.arguments}
                )
            )

    successful_names = {call.name for call in calls if call.successful}
    required = set(case.required_tools)
    allowed = required.union(case.optional_tools)
    missing = tuple(sorted(required.difference(successful_names)))
    unnecessary = tuple(sorted(successful_names.difference(allowed)))
    duplicates = len(raw_keys) - len(set(raw_keys))
    selection_error = bool(missing or unnecessary or duplicates)
    return tuple(calls), missing, unnecessary, duplicates, selection_error


async def run_trial(
    graph,
    *,
    variant: Variant,
    case: RoutingCase,
    repeat: int,
    semaphore: asyncio.Semaphore,
    max_client_retries: int,
) -> RoutingTrial:
    """Execute one retry-bounded live-model trajectory."""
    messages: list[object] = []
    routing: dict[str, object] = {}
    completed = False
    error_type: str | None = None
    attempts = 0
    started = time.perf_counter()
    for attempt in range(max_client_retries + 1):
        attempts = attempt + 1
        messages = []
        routing = {}
        try:
            async with semaphore:
                async for mode, payload in graph.astream(
                    {"messages": [HumanMessage(content=case.prompt)]},
                    config={"recursion_limit": 12},
                    stream_mode=["values"],
                ):
                    if mode != "values" or not isinstance(payload, dict):
                        continue
                    raw_messages = payload.get("messages")
                    if isinstance(raw_messages, list):
                        messages = cast(list[object], raw_messages)
                    raw_routing = payload.get("routing")
                    if isinstance(raw_routing, dict):
                        routing = cast(dict[str, object], raw_routing)
            completed = True
            error_type = None
            break
        except Exception as error:  # retain failed trials rather than hiding them
            error_type = type(error).__name__
            if error_type != "ClientError" or attempt == max_client_retries:
                break
            await asyncio.sleep(2**attempt)

    calls, missing, unnecessary, duplicates, selection_error = grade_calls(
        messages, case
    )
    selected = routing.get("selected_tool_names", [])
    return RoutingTrial(
        variant=variant,
        case_id=case.case_id,
        repeat=repeat,
        completed=completed,
        attempts=attempts,
        duration_ms=round((time.perf_counter() - started) * 1_000, 2),
        error_type=error_type,
        routing_mode=(
            str(routing["mode"]) if isinstance(routing.get("mode"), str) else None
        ),
        selected_tool_names=tuple(str(name) for name in selected)
        if isinstance(selected, list)
        else (),
        calls=calls,
        missing_required_tools=missing,
        unnecessary_tools=unnecessary,
        duplicate_tool_calls=duplicates,
        selection_error=(not completed) or selection_error,
    )


async def evaluate(
    cases: list[RoutingCase],
    *,
    repeats: int,
    concurrency: int,
    max_client_retries: int,
) -> list[RoutingTrial]:
    """Run paired trials with intent routing as the sole treatment variable."""
    tools = create_synthetic_financial_tools()
    model = create_model()
    graphs = {
        "fixed_16": build_graph(model, tools=tools, enable_intent_routing=False),
        "intent_routed": build_graph(model, tools=tools, enable_intent_routing=True),
    }
    semaphore = asyncio.Semaphore(concurrency)
    work = [
        run_trial(
            graphs[variant],
            variant=variant,
            case=case,
            repeat=repeat,
            semaphore=semaphore,
            max_client_retries=max_client_retries,
        )
        for repeat in range(1, repeats + 1)
        for case in cases
        for variant in cast(tuple[Variant, Variant], ("fixed_16", "intent_routed"))
    ]
    return list(await asyncio.gather(*work))


def variant_metrics(
    results: list[RoutingTrial],
    variant: Variant,
) -> dict[str, object]:
    selected = [result for result in results if result.variant == variant]
    errors = sum(result.selection_error for result in selected)
    durations = sorted(result.duration_ms for result in selected)
    return {
        "trials": len(selected),
        "completed_trials": sum(result.completed for result in selected),
        "selection_errors": errors,
        "selection_error_rate": errors / len(selected) if selected else 0.0,
        "missing_required_tool_trials": sum(
            bool(result.missing_required_tools) for result in selected
        ),
        "unnecessary_tool_trials": sum(
            bool(result.unnecessary_tools) for result in selected
        ),
        "duplicate_tool_calls": sum(result.duplicate_tool_calls for result in selected),
        "mean_selected_schemas": (
            sum(len(result.selected_tool_names) for result in selected) / len(selected)
            if selected
            else 0.0
        ),
        "p50_duration_ms": durations[len(durations) // 2] if durations else 0.0,
    }


def build_report(
    results: list[RoutingTrial],
    *,
    cases: list[RoutingCase],
    dataset_sha256: str,
    repeats: int,
) -> dict[str, object]:
    baseline = variant_metrics(results, "fixed_16")
    candidate = variant_metrics(results, "intent_routed")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": os.getenv("GEMINI_MODEL") or os.getenv("MODEL_NAME"),
        "query_count": len(cases),
        "repeats_per_query_per_variant": repeats,
        "total_trials": len(results),
        "dataset_sha256": dataset_sha256,
        "metric_definitions": {
            "selection_error": (
                "trial failed, a required tool did not execute successfully, an "
                "unlabeled tool executed successfully, or an equivalent call duplicated"
            ),
            "selected_schema": "one tool schema exposed to Gemini for the request",
        },
        "fixed_16": baseline,
        "intent_routed": candidate,
        "selection_error_change_percentage_points": (
            float(candidate["selection_error_rate"])
            - float(baseline["selection_error_rate"])
        )
        * 100,
        "relative_selected_schema_reduction": (
            (
                float(baseline["mean_selected_schemas"])
                - float(candidate["mean_selected_schemas"])
            )
            / float(baseline["mean_selected_schemas"])
            if baseline["mean_selected_schemas"]
            else None
        ),
        "trials": [
            {**asdict(result), "calls": [asdict(call) for call in result.calls]}
            for result in results
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/routing/cases_v1.json"),
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--max-client-retries", type=int, default=2)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evals/results/intent_routing_v1.json"),
    )
    args = parser.parse_args()
    if args.repeats <= 0 or args.concurrency <= 0 or args.max_client_retries < 0:
        parser.error("repeats/concurrency must be positive and retries non-negative")
    cases, dataset_sha256 = load_cases(args.cases)
    results = asyncio.run(
        evaluate(
            cases,
            repeats=args.repeats,
            concurrency=args.concurrency,
            max_client_retries=args.max_client_retries,
        )
    )
    report = build_report(
        results,
        cases=cases,
        dataset_sha256=dataset_sha256,
        repeats=args.repeats,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "trials"}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
