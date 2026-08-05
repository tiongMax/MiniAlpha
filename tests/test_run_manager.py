"""Phase 7 detached execution and cancellation tests."""

import asyncio
import json
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from app.api.main import create_app
from app.persistence.memory import InMemoryConversationRepository
from app.services.research_agent import (
    AgentStreamComplete,
    AgentStreamEvent,
    ResearchResult,
)
from app.services.run_manager import DetachedRunManager
from app.services.thread_research import ThreadResearchService
from tests.api_helpers import research_service
from tests.test_research_agent import SuccessfulGraph


class ControlledAgent:
    """Agent double that waits until the test allows graph completion."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_thread(self, message, **context):
        self.started.set()
        yield AgentStreamEvent("message_chunk", {"delta": "Partial answer."})
        await self.release.wait()
        yield AgentStreamComplete(
            ResearchResult(
                answer="Detached answer.",
                tool_calls=(),
                tool_results=(),
                artifacts=(),
                checkpoint_id="detached-checkpoint",
            )
        )


def test_run_completes_without_an_attached_event_consumer() -> None:
    """Execution ownership remains with the manager, not an SSE request."""

    async def exercise():
        repository = InMemoryConversationRepository()
        agent = ControlledAgent()
        manager = DetachedRunManager(ThreadResearchService(repository, agent))
        await manager.start()
        submission = await manager.submit(
            "Analyze Apple.", thread_id=None, request_key=uuid4()
        )
        await agent.started.wait()
        agent.release.set()
        await manager._queue.join()
        events = [event async for event in manager.events(submission.run_id)]
        stored = await repository.get_turn(submission.run_id)
        await manager.close()
        return events, stored

    events, stored = asyncio.run(exercise())
    assert [event.event for event in events] == [
        "metadata",
        "message_chunk",
        "run_end",
    ]
    assert stored is not None
    assert stored.run.status == "completed"
    assert stored.run.answer == "Detached answer."


def test_cancellation_is_durable_and_ends_all_event_attachments() -> None:
    """Stop interrupts the graph and commits cancelled before run_end."""

    async def exercise():
        repository = InMemoryConversationRepository()
        agent = ControlledAgent()
        manager = DetachedRunManager(ThreadResearchService(repository, agent))
        await manager.start()
        submission = await manager.submit(
            "Analyze Apple.", thread_id=None, request_key=uuid4()
        )
        await agent.started.wait()
        cancelled = await manager.cancel(submission.run_id)
        events = [event async for event in manager.events(submission.run_id)]
        stored = await repository.get_turn(submission.run_id)
        await manager.close()
        return cancelled, events, stored

    cancelled, events, stored = asyncio.run(exercise())
    assert cancelled.status == "cancelled"
    assert events[-1].event == "run_end"
    assert events[-1].data == {"status": "cancelled"}
    assert stored is not None
    assert stored.run.status == "cancelled"
    assert stored.run.error_code == "cancelled"
    assert stored.run.answer == "Partial answer."


def test_detached_http_admission_and_cancellation_contract() -> None:
    """The public API returns 202 before work and exposes durable Stop."""

    async def exercise():
        repository = InMemoryConversationRepository()
        agent = ControlledAgent()
        app = create_app(
            research_service(SuccessfulGraph()),
            ThreadResearchService(repository, agent),
        )
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                admitted = await client.post(
                    "/api/v1/threads/runs",
                    json={
                        "messages": [{"role": "user", "content": "Analyze Apple."}],
                        "request_key": str(uuid4()),
                    },
                )
                payload = admitted.json()
                await agent.started.wait()
                cancelled = await client.post(
                    f"/api/v1/runs/{payload['run_id']}/cancel"
                )
                streamed = await client.get(payload["events_url"])

        events = [
            json.loads(line.removeprefix("data: "))
            for line in streamed.text.splitlines()
            if line.startswith("data: ")
        ]
        return admitted, cancelled, events

    admitted, cancelled, events = asyncio.run(exercise())
    assert admitted.status_code == 202
    assert admitted.json()["status"] == "in_progress"
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert [event["event"] for event in events] == [
        "metadata",
        "message_chunk",
        "run_end",
    ]
    assert events[-1]["data"] == {"status": "cancelled"}
