"""Credential-free contracts for durable thread API routes."""

import asyncio
from uuid import UUID, uuid4

from app.api.main import create_app
from app.persistence.memory import InMemoryConversationRepository
from app.services.thread_research import ThreadResearchService
from tests.api_helpers import api_request, research_service
from tests.test_research_agent import SuccessfulGraph
from tests.test_thread_research import FailingAgent, SuccessfulAgent


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
