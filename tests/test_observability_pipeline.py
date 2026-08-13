"""Credential-free observability coverage across the real application pipeline."""

import asyncio
import json
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from app.agent.graph import build_graph
from app.agent.retry import RetryPolicy
from app.agent.tool_executor import IsolatedToolExecutor
from app.agent.tool_registry import ToolRegistry
from app.agent.tools import create_default_tools
from app.cache.models import CacheNamespace
from app.cache.service import ResearchResultCacheService
from app.cache.stores import CacheCoordinator, InMemoryExactCache, InMemorySemanticCache
from app.domain.company import CompanyOverview
from app.domain.errors import FinancialProviderError
from app.observability import (
    RecordingSpanSink,
    SpanRecord,
    observe_span,
    reset_span_sink,
    set_span_sink,
)
from app.persistence.postgres import PostgresConversationRepository
from app.providers.yahoo import YahooFinanceProvider
from app.services.research_agent import ResearchAgentService
from app.services.thread_research import ThreadResearchService

_PROMPT = "Analyze AAPL without exposing sk-observability-secret-123456."
_SYMBOL = "AAPL"
_ANSWER = "private-answer-sentinel-2468"
_COMPANY_NAME = "Artifact Sentinel Industries 9137"
_ARTIFACT_VALUE = 9_137_246_801.0
_NOW = datetime.now(UTC)


class _DeterministicEmbedder:
    async def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, 0.0)

    async def embed_document(self, _text: str) -> tuple[float, ...]:
        return (1.0, 0.0)


class _ToolCallingModel:
    """Credential-free model double that still exercises the compiled graph."""

    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, tools):
        assert "get_company_overview" in {tool.name for tool in tools}

        async def respond(messages):
            self.calls += 1
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(
                    content=_ANSWER,
                    usage_metadata={
                        "input_tokens": 17,
                        "output_tokens": 5,
                        "total_tokens": 22,
                    },
                )
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "observability-tool-call",
                        "name": "get_company_overview",
                        "args": {"symbol": _SYMBOL},
                        "type": "tool_call",
                    }
                ],
                usage_metadata={
                    "input_tokens": 11,
                    "output_tokens": 4,
                    "total_tokens": 15,
                },
            )

        return RunnableLambda(respond)


def _company_overview() -> CompanyOverview:
    return CompanyOverview(
        symbol=_SYMBOL,
        company_name=_COMPANY_NAME,
        exchange="Sentinel Exchange",
        currency="USD",
        sector="Technology",
        industry="Research",
        price=123.45,
        market_cap=_ARTIFACT_VALUE,
        trailing_pe=21.0,
        forward_pe=19.0,
        price_to_book=4.0,
        total_revenue=1_000_000.0,
        revenue_growth=0.1,
        operating_margin=0.2,
        profit_margin=0.15,
        total_cash=100_000.0,
        total_debt=50_000.0,
        dividend_yield=0.01,
        beta=1.1,
        provider="Credential-free Yahoo double",
        retrieved_at=_NOW,
    )


def _serialized_spans(spans: tuple[SpanRecord, ...]) -> str:
    payload = [
        {
            "name": span.name,
            "run_type": span.run_type,
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "status": span.status,
            "attributes": dict(span.attributes),
            "tags": list(span.tags),
            "duration_ms": span.duration_ms,
            "error_type": span.error_type,
        }
        for span in spans
    ]
    return json.dumps(payload, sort_keys=True)


def test_real_research_cache_graph_tool_and_provider_spans_are_attributable(
    monkeypatch,
) -> None:
    """Capture operational spans without credentials, network, or application data."""

    def fake_fetch(_provider: YahooFinanceProvider, symbol: str) -> CompanyOverview:
        assert symbol == _SYMBOL
        return _company_overview()

    monkeypatch.setattr(YahooFinanceProvider, "_fetch", fake_fetch)
    tools = list(create_default_tools())
    model = _ToolCallingModel()
    graph = build_graph(cast(BaseChatModel, model), tools=tools)
    cache = ResearchResultCacheService(
        CacheCoordinator(
            exact=InMemoryExactCache(clock=lambda: _NOW),
            semantic=InMemorySemanticCache(),
            embedder=_DeterministicEmbedder(),
            clock=lambda: _NOW,
        ),
        namespace=CacheNamespace(
            model="credential-free-model",
            prompt_version="test-v1",
            graph_version="test-v1",
            tool_schema_version="test-v1",
            embedding_model="credential-free-embedding",
            embedding_dimensions=2,
        ),
        registry=ToolRegistry(tools),
        clock=lambda: _NOW,
    )
    service = ResearchAgentService(graph, result_cache=cache)
    sink = RecordingSpanSink()
    token = set_span_sink(sink)
    try:
        first = asyncio.run(service.research(_PROMPT))
        second = asyncio.run(service.research(_PROMPT))
    finally:
        reset_span_sink(token)

    assert first.answer == second.answer == _ANSWER
    assert first.cache is not None and first.cache.status == "miss"
    assert second.cache is not None and second.cache.status == "exact_hit"
    assert first.usage.total_tokens == 37
    assert second.usage.total_tokens == 0
    assert model.calls == 2

    spans = sink.spans
    by_name: dict[str, list[SpanRecord]] = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)

    assert set(by_name) == {
        "mini_alpha.research_run",
        "cache.exact",
        "cache.embedding",
        "cache.semantic",
        "routing.decision",
        "model.invoke",
        "tool.execute",
        "provider.request",
    }
    assert len(by_name["mini_alpha.research_run"]) == 2
    assert len(by_name["model.invoke"]) == 2
    assert len(by_name["tool.execute"]) == 1
    assert len(by_name["provider.request"]) == 1
    assert sorted(
        span.attributes["total_tokens"] for span in by_name["model.invoke"]
    ) == [15, 22]

    roots = by_name["mini_alpha.research_run"]
    miss_root = next(
        span for span in roots if span.attributes["cache_status"] == "miss"
    )
    hit_root = next(
        span for span in roots if span.attributes["cache_status"] == "exact_hit"
    )
    assert miss_root.attributes["total_tokens"] == 37
    assert hit_root.attributes["total_tokens"] == 0
    assert all(span.status == "ok" and span.duration_ms >= 0 for span in spans)

    tool_span = by_name["tool.execute"][0]
    provider_span = by_name["provider.request"][0]
    assert tool_span.attributes["outcome"] == "ok"
    assert provider_span.attributes["outcome"] == "ok"
    assert provider_span.parent_span_id == tool_span.span_id
    miss_child_names = {
        span.name for span in spans if span.parent_span_id == miss_root.span_id
    }
    assert {
        "cache.exact",
        "cache.embedding",
        "cache.semantic",
        "routing.decision",
        "model.invoke",
        "tool.execute",
    } <= miss_child_names
    assert {span.name for span in spans if span.parent_span_id == hit_root.span_id} == {
        "cache.exact"
    }

    serialized = _serialized_spans(spans)
    for forbidden in (
        _PROMPT,
        _SYMBOL,
        "sk-observability-secret-123456",
        _ANSWER,
        _COMPANY_NAME,
        str(_ARTIFACT_VALUE),
    ):
        assert forbidden not in serialized


def test_real_repository_facade_records_safe_persistence_finalization() -> None:
    """Exercise the repository boundary while replacing only the database lifecycle."""

    completed_turn = object()

    class FakeLifecycle:
        async def complete_run(self, run_id, **kwargs):
            assert run_id == run_identifier
            assert kwargs["answer"] == _ANSWER
            assert kwargs["artifacts"][0]["data"]["company"] == _COMPANY_NAME
            return completed_turn

    run_identifier = uuid4()
    repository = PostgresConversationRepository.__new__(PostgresConversationRepository)
    repository._lifecycle = FakeLifecycle()  # type: ignore[attr-defined]
    sink = RecordingSpanSink()
    token = set_span_sink(sink)
    try:
        result = asyncio.run(
            repository.complete_run(
                run_identifier,
                expected_checkpoint_id=None,
                checkpoint_id="secret-checkpoint-sentinel",
                answer=_ANSWER,
                tool_calls=[
                    {"name": "get_company_overview", "args": {"symbol": _SYMBOL}}
                ],
                artifacts=[
                    {
                        "artifact_type": "company_overview",
                        "status": "ok",
                        "data": {"company": _COMPANY_NAME, "value": _ARTIFACT_VALUE},
                    }
                ],
            )
        )
    finally:
        reset_span_sink(token)

    assert result is completed_turn
    assert len(sink.spans) == 1
    span = sink.spans[0]
    assert span.name == "persistence.finalize"
    assert span.parent_span_id is None
    assert span.status == "ok"
    assert span.attributes["outcome"] == "ok"
    assert span.attributes["tool_call_count"] == 1
    assert span.attributes["artifact_count"] == 1
    assert span.duration_ms >= 0

    serialized = _serialized_spans(sink.spans)
    for forbidden in (
        str(run_identifier),
        "secret-checkpoint-sentinel",
        _ANSWER,
        _SYMBOL,
        _COMPANY_NAME,
        str(_ARTIFACT_VALUE),
    ):
        assert forbidden not in serialized


def test_threaded_root_owns_durable_persistence_finalization() -> None:
    """Keep the persistence span under the same threaded research root."""
    from app.persistence.memory import InMemoryConversationRepository
    from app.services.research_agent import ResearchResult

    class RecordingRepository(InMemoryConversationRepository):
        async def complete_run(self, run_id, **kwargs):
            with observe_span(
                "persistence.finalize",
                metadata={"persistence_operation": "complete_run"},
            ) as span:
                result = await super().complete_run(run_id, **kwargs)
                span.set_attribute("outcome", "ok")
                return result

    class ThreadAgent:
        async def research_thread(self, _message, **_context):
            return ResearchResult(
                answer=_ANSWER,
                tool_calls=(),
                tool_results=(),
                artifacts=(),
                checkpoint_id="checkpoint-observability",
            )

    repository = RecordingRepository()
    service = ThreadResearchService(repository, ThreadAgent())
    sink = RecordingSpanSink()
    token = set_span_sink(sink)
    try:
        asyncio.run(
            service.research(
                _PROMPT,
                thread_id=None,
                request_key=uuid4(),
            )
        )
    finally:
        reset_span_sink(token)

    root = next(span for span in sink.spans if span.name == "mini_alpha.research_run")
    persistence = next(
        span for span in sink.spans if span.name == "persistence.finalize"
    )
    assert persistence.parent_span_id == root.span_id
    assert root.attributes["outcome"] == "ok"


def test_tool_span_reports_terminal_retry_attempt() -> None:
    """Attribute a recovered provider call to its actual second attempt."""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool

    attempts = 0

    @tool(response_format="content_and_artifact")
    async def flaky_tool() -> tuple[str, dict[str, object]]:
        """Fail once, then return a safe synthetic artifact."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FinancialProviderError("private upstream response")
        return "ok", {
            "artifact_type": "synthetic",
            "schema_version": 1,
            "status": "ok",
            "data": {},
        }

    executor = IsolatedToolExecutor(
        [flaky_tool],
        timeout_seconds=1,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    sink = RecordingSpanSink()
    token = set_span_sink(sink)
    try:
        asyncio.run(
            executor.ainvoke(
                {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "flaky_tool",
                                    "args": {},
                                    "id": "private-id",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            )
        )
    finally:
        reset_span_sink(token)

    span = sink.spans[0]
    assert attempts == 2
    assert span.name == "tool.execute"
    assert span.attributes["attempt"] == 2
    assert span.status == "ok"
    assert "private upstream response" not in _serialized_spans(sink.spans)
