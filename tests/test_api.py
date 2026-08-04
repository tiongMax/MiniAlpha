"""Credential-free contracts for the stateless and durable FastAPI routes."""

import asyncio
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

import app.api.main as api_main
from app.api.main import create_app
from app.persistence.memory import InMemoryConversationRepository
from app.services.research_agent import ResearchAgentService, ResearchGraph
from app.services.thread_research import ThreadResearchService
from tests.test_research_agent import AnswerlessGraph, SuccessfulGraph
from tests.test_thread_research import FailingAgent, SuccessfulAgent


class FakePersistenceRuntime:
    """Owned runtime double recording application shutdown."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        """Record pool cleanup."""
        self.closed = True


def research_service(graph: object) -> ResearchAgentService:
    """Wrap a deterministic graph double with an explicit protocol cast."""
    return ResearchAgentService(cast(ResearchGraph, graph))


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
    app = create_app(research_service(SuccessfulGraph()))

    response = api_request(app, "GET", "/health")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json() == {
        "status": "ok",
        "service": "mini-alpha",
        "phase": 5,
    }


def test_openapi_describes_phase_five_routes() -> None:
    """Verify generated documentation advertises both delivery modes."""
    app = create_app(research_service(SuccessfulGraph()))

    response = api_request(app, "GET", "/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == "0.5.0"
    assert "durable thread routes" in schema["info"]["description"]
    assert set(schema["paths"]) >= {
        "/health",
        "/ready",
        "/api/v1/research",
        "/api/v1/threads",
        "/api/v1/threads/messages",
        "/api/v1/threads/{thread_id}",
        "/api/v1/threads/{thread_id}/messages",
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
        "phase": 5,
        "persistence": "ready",
    }


def test_thread_message_workflow_and_idempotent_replay() -> None:
    """Verify create, continue, replay, list, detail, and transcript contracts."""
    repository = InMemoryConversationRepository()
    agent = SuccessfulAgent()
    app = create_app(
        research_service(SuccessfulGraph()),
        ThreadResearchService(repository, agent),
    )
    first_key = str(uuid4())
    second_key = str(uuid4())

    first = api_request(
        app,
        "POST",
        "/api/v1/threads/messages",
        json={
            "messages": [{"role": "user", "content": "  Analyze Apple.  "}],
            "request_key": first_key,
        },
    )
    thread_id = first.json()["thread_id"]
    second = api_request(
        app,
        "POST",
        f"/api/v1/threads/{thread_id}/messages",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Now compare it with Microsoft.",
                }
            ],
            "request_key": second_key,
        },
    )
    replay = api_request(
        app,
        "POST",
        f"/api/v1/threads/{thread_id}/messages",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Now compare it with Microsoft.",
                }
            ],
            "request_key": second_key,
        },
    )
    listing = api_request(app, "GET", "/api/v1/threads")
    detail = api_request(app, "GET", f"/api/v1/threads/{thread_id}")
    transcript = api_request(
        app,
        "GET",
        f"/api/v1/threads/{thread_id}/messages",
    )

    assert first.status_code == 200
    assert first.headers["Content-Location"].endswith(f"/{thread_id}/messages")
    assert first.json()["turn_index"] == 1
    assert second.status_code == 200
    assert second.json()["turn_index"] == 2
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["run_id"] == second.json()["run_id"]
    assert len(agent.calls) == 2
    assert listing.json()["total"] == 1
    assert detail.json()["thread_id"] == thread_id
    assert [turn["message"] for turn in transcript.json()["turns"]] == [
        "Analyze Apple.",
        "Now compare it with Microsoft.",
    ]


def test_thread_contracts_and_not_found_errors() -> None:
    """Verify durable routes reject malformed input and unknown threads."""
    app = create_app(
        research_service(SuccessfulGraph()),
        ThreadResearchService(
            InMemoryConversationRepository(),
            SuccessfulAgent(),
        ),
    )
    malformed = api_request(
        app,
        "POST",
        "/api/v1/threads/messages",
        json={
            "messages": [
                {"role": "user", "content": "One"},
                {"role": "user", "content": "Two"},
            ]
        },
    )
    unknown = api_request(
        app,
        "GET",
        f"/api/v1/threads/{uuid4()}",
    )

    assert malformed.status_code == 422
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "thread_not_found"


def test_thread_idempotency_and_active_run_errors() -> None:
    """Verify duplicate identities and concurrent turns use stable conflicts."""
    repository = InMemoryConversationRepository()
    service = ThreadResearchService(repository, SuccessfulAgent())
    app = create_app(research_service(SuccessfulGraph()), service)
    request_key = str(uuid4())
    first = api_request(
        app,
        "POST",
        "/api/v1/threads/messages",
        json={
            "messages": [{"role": "user", "content": "Analyze Apple."}],
            "request_key": request_key,
        },
    )
    thread_id = first.json()["thread_id"]
    key_conflict = api_request(
        app,
        "POST",
        f"/api/v1/threads/{thread_id}/messages",
        json={
            "messages": [{"role": "user", "content": "Different input."}],
            "request_key": request_key,
        },
    )
    active = asyncio.run(
        repository.admit_run(
            thread_id=UUID(thread_id),
            message="Admitted but not executed.",
            request_key=uuid4(),
        )
    )
    run_conflict = api_request(
        app,
        "POST",
        f"/api/v1/threads/{thread_id}/messages",
        json={
            "messages": [{"role": "user", "content": "Another request."}],
            "request_key": str(uuid4()),
        },
    )

    assert key_conflict.status_code == 409
    assert key_conflict.json()["error"]["code"] == "request_key_conflict"
    assert active.run.status == "in_progress"
    assert run_conflict.status_code == 409
    assert run_conflict.json()["error"]["code"] == "run_in_progress"


def test_thread_failure_replay_and_unavailable_persistence() -> None:
    """Verify terminal failures replay and missing composition returns 503."""
    agent = FailingAgent()
    app = create_app(
        research_service(SuccessfulGraph()),
        ThreadResearchService(InMemoryConversationRepository(), agent),
    )
    request_key = str(uuid4())
    body = {
        "messages": [{"role": "user", "content": "Analyze Apple."}],
        "request_key": request_key,
    }

    failed = api_request(app, "POST", "/api/v1/threads/messages", json=body)
    replay = api_request(app, "POST", "/api/v1/threads/messages", json=body)
    unavailable = api_request(
        create_app(research_service(SuccessfulGraph())),
        "POST",
        "/api/v1/threads/messages",
        json=body,
    )

    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "research_failed"
    assert replay.status_code == 502
    assert replay.json() == failed.json()
    assert agent.calls == 1
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "persistence_unavailable"


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
