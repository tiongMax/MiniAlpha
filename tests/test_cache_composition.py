"""Production stateless cache composition remains optional and fail-open."""

import asyncio
from types import SimpleNamespace

import pytest

import app.api.dependencies as dependencies
from app.services.research_agent import ResearchAgentService


class FakeSecret:
    def get_secret_value(self) -> str:
        return "test-key"


class FakeModel:
    model = "test-model"
    google_api_key = FakeSecret()

    def bind_tools(self, _tools):
        return self


def test_cache_startup_failure_returns_uncached_research_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dependencies, "create_model", FakeModel)
    monkeypatch.setattr(dependencies, "create_default_tools", lambda: [])
    monkeypatch.setattr(dependencies, "get_boolean", lambda *_args: True)
    monkeypatch.setattr(dependencies, "get_redis_url", lambda: "redis://test")
    monkeypatch.setattr(dependencies, "get_database_url", lambda: "postgres://test")

    async def fail_open(**_kwargs):
        raise ConnectionError("cache unavailable")

    monkeypatch.setattr(dependencies.CacheRuntime, "open", fail_open)
    monkeypatch.setattr(
        dependencies,
        "build_graph",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )

    service, runtime = asyncio.run(dependencies.create_research_service())

    assert isinstance(service, ResearchAgentService)
    assert service._result_cache is None
    assert runtime is None
