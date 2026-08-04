"""Credential-free contracts for system and stateless API routes."""

import asyncio

import pytest

import app.api.main as api_main
from app.api.main import create_app
from app.persistence.memory import InMemoryConversationRepository
from app.services.research_agent import ResearchAgentService
from app.services.thread_research import ThreadResearchService
from tests.api_helpers import (
    FakePersistenceRuntime,
    api_request,
    research_service,
)
from tests.test_research_agent import AnswerlessGraph, SuccessfulGraph
from tests.test_thread_research import SuccessfulAgent


def test_health_is_independent_of_external_services() -> None:
    """Verify liveness reports the service and current phase."""
    app = create_app(research_service(SuccessfulGraph()))

    response = api_request(app, "GET", "/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json() == {
        "status": "ok",
        "service": "mini-alpha",
        "phase": 6,
    }


def test_openapi_describes_phase_six_routes() -> None:
    """Verify generated documentation advertises both delivery modes."""
    app = create_app(research_service(SuccessfulGraph()))

    response = api_request(app, "GET", "/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == "0.6.0"
    assert "durable thread routes" in schema["info"]["description"]
    assert set(schema["paths"]) >= {
        "/health",
        "/ready",
        "/api/v1/research",
        "/api/v1/threads",
        "/api/v1/threads/messages",
        "/api/v1/threads/messages/stream",
        "/api/v1/threads/{thread_id}",
        "/api/v1/threads/{thread_id}/messages",
        "/api/v1/threads/{thread_id}/messages/stream",
    }


def test_research_returns_answer_and_evidence() -> None:
    """Verify the HTTP response preserves tool calls and artifacts."""
    app = create_app(research_service(SuccessfulGraph()))

    response = api_request(
        app,
        "POST",
        "/api/v1/research",
        json={"message": "  Analyze Apple.  "},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Apple is highly profitable."
    assert payload["tool_calls"] == [
        {
            "name": "get_company_overview",
            "arguments": {"symbol": "AAPL"},
        }
    ]
    assert payload["artifacts"][0]["artifact_type"] == "company_overview"
    assert payload["artifacts"][0]["data"]["symbol"] == "AAPL"


def test_readiness_requires_persistent_composition() -> None:
    """Verify injected applications distinguish liveness from readiness."""
    service = research_service(SuccessfulGraph())
    unavailable = create_app(service)
    ready = create_app(
        service,
        ThreadResearchService(
            InMemoryConversationRepository(),
            SuccessfulAgent(),
        ),
    )

    unavailable_response = api_request(unavailable, "GET", "/ready")
    ready_response = api_request(ready, "GET", "/ready")

    assert unavailable_response.status_code == 503
    assert unavailable_response.json()["error"]["code"] == ("persistence_unavailable")
    assert ready_response.status_code == 200
    assert ready_response.json() == {
        "status": "ready",
        "service": "mini-alpha",
        "phase": 6,
        "persistence": "ready",
    }


def test_research_rejects_blank_input_and_unknown_fields() -> None:
    """Verify the public request contract is strict."""
    app = create_app(research_service(SuccessfulGraph()))

    blank = api_request(
        app,
        "POST",
        "/api/v1/research",
        json={"message": "   "},
    )
    extra = api_request(
        app,
        "POST",
        "/api/v1/research",
        json={"message": "Analyze Apple.", "thread_id": "not-supported"},
    )

    assert blank.status_code == 422
    assert extra.status_code == 422


def test_research_failure_uses_stable_error_envelope() -> None:
    """Verify graph completion failures do not expose internal details."""
    app = create_app(research_service(AnswerlessGraph()))

    response = api_request(
        app,
        "POST",
        "/api/v1/research",
        json={"message": "Analyze Apple."},
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": {
            "code": "research_failed",
            "message": "The research agent could not complete the request.",
        }
    }


def test_missing_configuration_preserves_liveness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify bad production configuration yields liveness and a stable 503."""

    def fail_composition() -> ResearchAgentService:
        raise RuntimeError("Missing GEMINI_API_KEY")

    async def fail_persistence() -> tuple[ThreadResearchService, object]:
        raise RuntimeError("Missing DATABASE_URL")

    monkeypatch.setattr(api_main, "create_research_service", fail_composition)
    monkeypatch.setattr(
        api_main,
        "create_thread_research_service",
        fail_persistence,
    )
    app = create_app()

    health = api_request(app, "GET", "/health")
    research = api_request(
        app,
        "POST",
        "/api/v1/research",
        json={"message": "Analyze Apple."},
    )

    assert health.status_code == 200
    assert research.status_code == 503
    assert research.json() == {
        "error": {
            "code": "research_unavailable",
            "message": "The research service is not configured.",
        }
    }


def test_application_closes_owned_persistence_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify production composition releases its PostgreSQL pool."""
    runtime = FakePersistenceRuntime()
    durable_service = ThreadResearchService(
        InMemoryConversationRepository(),
        SuccessfulAgent(),
    )

    async def compose_persistence():
        return durable_service, runtime

    monkeypatch.setattr(
        api_main,
        "create_research_service",
        lambda: research_service(SuccessfulGraph()),
    )
    monkeypatch.setattr(
        api_main,
        "create_thread_research_service",
        compose_persistence,
    )
    app = create_app()

    async def enter_and_exit() -> None:
        async with app.router.lifespan_context(app):
            assert runtime.closed is False

    asyncio.run(enter_and_exit())

    assert runtime.closed is True
