"""Credential-free contract tests for the Phase 3 FastAPI layer."""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

import app.api.main as api_main
from app.api.main import create_app
from app.services.research_agent import ResearchAgentService
from tests.test_research_agent import AnswerlessGraph, SuccessfulGraph


def api_request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    json: object | None = None,
) -> Response:
    """Send one request while running the application's ASGI lifespan."""

    async def send() -> Response:
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, json=json)

    return asyncio.run(send())


def test_health_is_independent_of_external_services() -> None:
    """Verify liveness reports the service and current phase."""
    app = create_app(ResearchAgentService(SuccessfulGraph()))

    response = api_request(app, "GET", "/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json() == {
        "status": "ok",
        "service": "mini-alpha",
        "phase": 3,
    }


def test_research_returns_answer_and_evidence() -> None:
    """Verify the HTTP response preserves tool calls and artifacts."""
    app = create_app(ResearchAgentService(SuccessfulGraph()))

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


def test_research_rejects_blank_input_and_unknown_fields() -> None:
    """Verify the public request contract is strict."""
    app = create_app(ResearchAgentService(SuccessfulGraph()))

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
    app = create_app(ResearchAgentService(AnswerlessGraph()))

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

    monkeypatch.setattr(api_main, "create_research_service", fail_composition)
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
