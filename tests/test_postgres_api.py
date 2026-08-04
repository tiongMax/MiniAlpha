"""Optional end-to-end HTTP test for PostgreSQL-backed threads."""

import asyncio
import os
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models.chat_models import BaseChatModel

from app.agent.graph import build_graph
from app.api.main import create_app
from app.persistence.runtime import PersistenceRuntime
from app.services.research_agent import ResearchAgentService, ResearchGraph
from app.services.thread_research import ThreadResearchService
from tests.test_research_agent import RememberingModel, SuccessfulGraph

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured",
)
def test_postgres_thread_api_remembers_the_previous_turn() -> None:
    """Verify the complete synchronous durable HTTP path."""
    database_url = TEST_DATABASE_URL
    assert database_url is not None

    async def exercise() -> None:
        runtime = await PersistenceRuntime.open(database_url)
        thread_id = None
        try:
            graph = build_graph(
                cast(BaseChatModel, RememberingModel()),
                tools=[],
                checkpointer=runtime.checkpointer,
            )
            thread_service = ThreadResearchService(
                runtime.repository,
                ResearchAgentService(cast(ResearchGraph, graph)),
            )
            app = create_app(
                ResearchAgentService(cast(ResearchGraph, SuccessfulGraph())),
                thread_service,
            )
            transport = ASGITransport(app=app)
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=transport,
                    base_url="http://testserver",
                ) as client:
                    first = await client.post(
                        "/api/v1/threads/messages",
                        json={
                            "messages": [
                                {
                                    "role": "user",
                                    "content": "Analyze Apple.",
                                }
                            ],
                            "request_key": str(uuid4()),
                        },
                    )
                    thread_id = UUID(first.json()["thread_id"])
                    second = await client.post(
                        f"/api/v1/threads/{thread_id}/messages",
                        json={
                            "messages": [
                                {
                                    "role": "user",
                                    "content": ("Now compare it with Microsoft."),
                                }
                            ],
                            "request_key": str(uuid4()),
                        },
                    )

            assert first.status_code == 200
            assert second.status_code == 200
            assert second.json()["answer"] == (
                "Analyze Apple. | Now compare it with Microsoft."
            )
        finally:
            if thread_id is not None:
                await runtime.checkpointer.adelete_thread(str(thread_id))
                async with runtime.pool.connection() as connection:
                    await connection.execute(
                        """
                        DELETE FROM conversation_threads
                        WHERE conversation_thread_id = %s
                        """,
                        (thread_id,),
                    )
            await runtime.close()

    asyncio.run(
        exercise(),
        loop_factory=asyncio.SelectorEventLoop,
    )
