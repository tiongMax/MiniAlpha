"""Run a locked, deterministic exact/semantic result-cache experiment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from langchain_core.tools import StructuredTool

from app.agent.tool_registry import ToolRegistry
from app.cache.models import CacheNamespace
from app.cache.service import ResearchResultCacheService
from app.cache.stores import CacheCoordinator, InMemoryExactCache, InMemorySemanticCache
from app.services.research_agent import (
    ExecutedToolCall,
    ExecutedToolResult,
    ModelUsage,
    ResearchResult,
)

Variant = Literal["cache_disabled", "cache_enabled"]


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    kind: str
    prompt: str
    origin_key: str
    expected_tier: str


@dataclass(frozen=True, slots=True)
class Trial:
    variant: Variant
    case_id: str
    kind: str
    expected_tier: str
    observed_tier: str
    correct: bool
    generation_tokens: int
    duration_ms: float


class HashEmbedder:
    """Credential-free semantic embedding for reproducible cache-policy tests."""

    _groups = {
        "aapl_volatility_1y": (1.0, 0.0, 0.0, 0.0),
        "msft_volatility_1y": (0.0, 1.0, 0.0, 0.0),
        "aapl_volatility_5y": (0.0, 0.0, 1.0, 0.0),
        "aapl_overview": (0.0, 0.0, 0.0, 1.0),
    }

    async def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    async def embed_document(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    def _vector(self, text: str) -> tuple[float, ...]:
        normalized = text.casefold()
        if "overview" in normalized:
            return self._groups["aapl_overview"]
        if "msft" in normalized:
            return self._groups["msft_volatility_1y"]
        if "5y" in normalized:
            return self._groups["aapl_volatility_5y"]
        return self._groups["aapl_volatility_1y"]


class SyntheticOrigin:
    """Stable origin with measured latency and generation usage."""

    async def research(self, case: Case) -> ResearchResult:
        await asyncio.sleep(0.01)
        if case.origin_key == "provider_error":
            status = "error"
            artifact: dict[str, object] = {
                "artifact_type": "volatility_analysis",
                "schema_version": 1,
                "status": "error",
                "error": "Synthetic provider unavailable.",
            }
        else:
            status = "ok"
            artifact = {
                "artifact_type": (
                    "company_overview"
                    if "overview" in case.origin_key
                    else "company_news"
                    if "news" in case.origin_key
                    else "volatility_analysis"
                ),
                "schema_version": 1,
                "status": "ok",
                "data": {
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "source_retrieved_at": datetime.now(UTC).isoformat(),
                    "origin_key": case.origin_key,
                },
            }
        return ResearchResult(
            answer=f"Synthetic answer for {case.origin_key}.",
            tool_calls=(
                ExecutedToolCall(
                    name="calculate_volatility",
                    arguments={"origin_key": case.origin_key},
                    status=cast(Literal["ok", "error"], status),
                ),
            ),
            tool_results=(
                ExecutedToolResult(
                    name="calculate_volatility",
                    content="synthetic",
                    artifact=artifact,
                ),
            ),
            artifacts=(artifact,),
            checkpoint_id=None,
            usage=ModelUsage(input_tokens=80, output_tokens=20, total_tokens=100),
        )


async def _stub(**_arguments: object) -> str:
    return "ok"


def load_cases(path: Path) -> tuple[list[Case], str]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("cache corpus requires schema_version 1 and cases")
    cases = [Case(**item) for item in payload["cases"]]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("cache case IDs must be unique")
    return cases, hashlib.sha256(raw).hexdigest()


def cache_service() -> ResearchResultCacheService:
    tool = StructuredTool.from_function(
        coroutine=_stub,
        name="calculate_volatility",
        description="Calculate volatility.",
    )
    overview = StructuredTool.from_function(
        coroutine=_stub,
        name="get_company_overview",
        description="Get company overview.",
    )
    news = StructuredTool.from_function(
        coroutine=_stub,
        name="get_company_news",
        description="Get news.",
    )
    return ResearchResultCacheService(
        CacheCoordinator(
            exact=InMemoryExactCache(),
            semantic=InMemorySemanticCache(),
            embedder=HashEmbedder(),
            semantic_threshold=0.99,
        ),
        namespace=CacheNamespace(
            model="synthetic",
            prompt_version="1",
            graph_version="1",
            tool_schema_version="1",
            embedding_model="hash-v1",
            embedding_dimensions=4,
        ),
        registry=ToolRegistry([tool, overview, news]),
    )


async def run(cases: list[Case]) -> list[Trial]:
    trials: list[Trial] = []
    origin = SyntheticOrigin()
    cache = cache_service()
    for variant in cast(tuple[Variant, Variant], ("cache_disabled", "cache_enabled")):
        for case in cases:
            started = time.perf_counter()
            cached = (
                await cache.lookup(case.prompt) if variant == "cache_enabled" else None
            )
            if cached is None:
                result = await origin.research(case)
                observed = "miss"
                if variant == "cache_enabled":
                    await cache.store(case.prompt, result)
            else:
                result = cached.result
                observed = cached.status
            current_tokens = 0 if cached is not None else result.usage.total_tokens
            expected = case.expected_tier if variant == "cache_enabled" else "miss"
            trials.append(
                Trial(
                    variant=variant,
                    case_id=case.case_id,
                    kind=case.kind,
                    expected_tier=expected,
                    observed_tier=observed,
                    correct=observed == expected,
                    generation_tokens=current_tokens,
                    duration_ms=round((time.perf_counter() - started) * 1_000, 3),
                )
            )
    return trials


def metrics(trials: list[Trial], variant: Variant) -> dict[str, object]:
    selected = [trial for trial in trials if trial.variant == variant]
    durations = sorted(trial.duration_ms for trial in selected)
    return {
        "requests": len(selected),
        "generation_tokens": sum(trial.generation_tokens for trial in selected),
        "correct_outcomes": sum(trial.correct for trial in selected),
        "exact_hits": sum(trial.observed_tier == "exact_hit" for trial in selected),
        "semantic_hits": sum(
            trial.observed_tier == "semantic_hit" for trial in selected
        ),
        "p50_duration_ms": statistics.median(durations),
    }


def build_report(trials: list[Trial], digest: str) -> dict[str, object]:
    baseline = metrics(trials, "cache_disabled")
    candidate = metrics(trials, "cache_enabled")
    baseline_tokens = int(baseline["generation_tokens"])
    candidate_tokens = int(candidate["generation_tokens"])
    exact_latencies = [
        trial.duration_ms
        for trial in trials
        if trial.variant == "cache_enabled" and trial.observed_tier == "exact_hit"
    ]
    cold_latencies = [
        trial.duration_ms
        for trial in trials
        if trial.variant == "cache_enabled" and trial.observed_tier == "miss"
    ]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": digest,
        "metric_scope": (
            "generation tokens only; embeddings are deterministic local vectors"
        ),
        "cache_disabled": baseline,
        "cache_enabled": candidate,
        "generation_token_reduction": (
            (baseline_tokens - candidate_tokens) / baseline_tokens
            if baseline_tokens
            else 0.0
        ),
        "warm_exact_p50_ms": statistics.median(exact_latencies),
        "cold_miss_p50_ms": statistics.median(cold_latencies),
        "false_semantic_hits": sum(
            not trial.correct and trial.observed_tier == "semantic_hit"
            for trial in trials
        ),
        "trials": [asdict(trial) for trial in trials],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("evals/cache/cases_v1.json"))
    parser.add_argument(
        "--output", type=Path, default=Path("evals/results/cache_v1.json")
    )
    args = parser.parse_args()
    cases, digest = load_cases(args.cases)
    report = build_report(asyncio.run(run(cases)), digest)
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
