"""Complete-result cache integration and safety contracts."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal, cast
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from app.agent.tool_registry import ToolRegistry
from app.cache.models import CacheNamespace
from app.cache.research_results import (
    deserialize_research_result,
    serialize_research_result,
)
from app.cache.service import ResearchResultCacheService
from app.cache.stores import CacheCoordinator, InMemoryExactCache, InMemorySemanticCache
from app.services.research_agent import (
    ExecutedToolCall,
    ExecutedToolResult,
    ModelUsage,
    ResearchAgentService,
    ResearchGraph,
    ResearchResult,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


async def _tool(**_arguments: object) -> str:
    return "ok"


def registry() -> ToolRegistry:
    return ToolRegistry(
        [
            StructuredTool.from_function(
                coroutine=_tool,
                name="calculate_volatility",
                description="Calculate volatility.",
            )
        ]
    )


class SimilarEmbedder:
    async def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, 0.0)

    async def embed_document(self, _text: str) -> tuple[float, ...]:
        return (1.0, 0.0)


class LockingExact(InMemoryExactCache):
    def __init__(self) -> None:
        super().__init__(clock=lambda: NOW)
        self.token: str | None = None

    async def acquire_fill_lock(self, _key: str, *, ttl_seconds: int):
        assert ttl_seconds > 0
        if self.token is not None:
            return None
        self.token = "owner"
        return self.token

    async def release_fill_lock(self, _key: str, token: str):
        assert token == self.token
        self.token = None
        return True


def result(*, status: str = "ok") -> ResearchResult:
    artifact = {
        "artifact_type": "volatility_analysis",
        "schema_version": 1,
        "status": status,
        "data": {
            "source_retrieved_at": NOW.isoformat(),
            "symbols": ["AAPL"],
        },
    }
    if status == "error":
        artifact.pop("data")
        artifact["error"] = "Unavailable."
    return ResearchResult(
        answer="AAPL volatility is 20%.",
        tool_calls=(
            ExecutedToolCall(
                name="calculate_volatility",
                arguments={"symbol": "AAPL", "period": "1y"},
                status=cast(Literal["ok", "error"], status),
            ),
        ),
        tool_results=(
            ExecutedToolResult(
                name="calculate_volatility",
                content="20%",
                artifact=artifact,
            ),
        ),
        artifacts=(artifact,),
        checkpoint_id=None,
        usage=ModelUsage(input_tokens=100, output_tokens=20, total_tokens=120),
    )


def cache_service() -> ResearchResultCacheService:
    return ResearchResultCacheService(
        CacheCoordinator(
            exact=InMemoryExactCache(clock=lambda: NOW),
            semantic=InMemorySemanticCache(),
            embedder=SimilarEmbedder(),
            clock=lambda: NOW,
        ),
        namespace=CacheNamespace(
            model="test",
            prompt_version="1",
            graph_version="1",
            tool_schema_version="1",
            embedding_model="test",
            embedding_dimensions=2,
        ),
        registry=registry(),
        clock=lambda: NOW,
    )


def test_result_serialization_round_trips_usage_and_artifacts() -> None:
    original = result()

    restored = deserialize_research_result(serialize_research_result(original))

    assert restored == original


def test_exact_and_semantic_hits_skip_current_generation_tokens() -> None:
    class NeverGraph:
        async def ainvoke(self, _input, config=None):
            raise AssertionError("cache hit must skip the graph")

    async def scenario() -> None:
        cache = cache_service()
        await cache.store("Show AAPL volatility over 1y", result())
        agent = ResearchAgentService(
            cast(ResearchGraph, NeverGraph()), result_cache=cache
        )

        exact = await agent.research("Show AAPL volatility over 1y")
        semantic = await agent.research("Calculate AAPL volatility over 1y")

        assert exact.cache is not None and exact.cache.status == "exact_hit"
        assert semantic.cache is not None and semantic.cache.status == "semantic_hit"
        assert exact.usage.total_tokens == semantic.usage.total_tokens == 0
        assert exact.cache.origin_usage is not None
        assert exact.cache.origin_usage.total_tokens == 120

    asyncio.run(scenario())


def test_error_result_and_malformed_payload_are_never_hits() -> None:
    async def scenario() -> None:
        cache = cache_service()
        await cache.store("Show AAPL volatility over 1y", result(status="error"))
        assert await cache.lookup("Show AAPL volatility over 1y") is None

        malformed = serialize_research_result(result())
        malformed["answer"] = ""
        try:
            deserialize_research_result(malformed)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected malformed cache payload rejection")

    asyncio.run(scenario())


def test_thread_execution_never_consults_stateless_result_cache() -> None:
    class RaisingCache:
        called = False

        async def lookup(self, _message):
            self.called = True
            raise AssertionError("thread must bypass cache")

        async def store(self, _message, _result):
            self.called = True
            raise AssertionError("thread must bypass cache")

    class MarkerGraph:
        async def ainvoke(self, input, config=None):
            assert isinstance(input["messages"][0], HumanMessage)
            return {"messages": [*input["messages"], AIMessage(content="Answer.")]}

        async def aget_state(self, config, *, subgraphs=False):
            return SimpleNamespace(
                config={"configurable": {"checkpoint_id": "checkpoint"}}
            )

    cache = RaisingCache()
    agent = ResearchAgentService(cast(ResearchGraph, MarkerGraph()), result_cache=cache)
    threaded = asyncio.run(
        agent.research_thread(
            "Show AAPL volatility.",
            thread_id=uuid4(),
            run_id=uuid4(),
            checkpoint_id=None,
        )
    )

    assert threaded.answer == "Answer."
    assert cache.called is False


def test_concurrent_exact_misses_use_one_origin_generation() -> None:
    from langchain_core.messages import AIMessage

    class CountingGraph:
        calls = 0

        async def ainvoke(self, input, config=None):
            self.calls += 1
            await asyncio.sleep(0.1)
            return {
                "messages": [
                    *input["messages"],
                    AIMessage(
                        content="Cached conceptual answer.",
                        usage_metadata={
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        },
                    ),
                ]
            }

    async def scenario() -> None:
        exact = LockingExact()
        cache = ResearchResultCacheService(
            CacheCoordinator(exact=exact, clock=lambda: NOW),
            namespace=CacheNamespace(
                model="test",
                prompt_version="1",
                graph_version="1",
                tool_schema_version="1",
            ),
            registry=registry(),
            fill_wait_seconds=1,
            clock=lambda: NOW,
        )
        graph = CountingGraph()
        agent = ResearchAgentService(cast(ResearchGraph, graph), result_cache=cache)

        first, second = await asyncio.gather(
            agent.research("Explain volatility."),
            agent.research("Explain volatility."),
        )

        assert graph.calls == 1
        assert {first.cache.status, second.cache.status} == {"miss", "exact_hit"}
        assert sorted([first.usage.total_tokens, second.usage.total_tokens]) == [0, 15]

    asyncio.run(scenario())
