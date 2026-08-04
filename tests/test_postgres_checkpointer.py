"""Optional integration test for PostgreSQL-backed LangGraph memory."""

import asyncio
import os
from typing import cast
from uuid import uuid4

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agent.graph import build_graph
from app.services.research_agent import ResearchAgentService, ResearchGraph

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class RememberingModel:
    """Credential-free model that echoes accumulated user messages."""

    def bind_tools(self, _tools):
        """Return a runnable final-answer model."""

        async def respond(messages):
            return AIMessage(
                content=" | ".join(
                    str(message.content)
                    for message in messages
                    if isinstance(message, HumanMessage)
                )
            )

        return RunnableLambda(respond)


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured",
)
def test_postgres_checkpointer_preserves_thread_context() -> None:
    """Verify conversation memory survives separate graph invocations."""
    database_url = TEST_DATABASE_URL
    assert database_url is not None

    async def exercise() -> None:
        thread_id = uuid4()
        async with AsyncPostgresSaver.from_conn_string(
            database_url,
        ) as checkpointer:
            graph = build_graph(
                cast(BaseChatModel, RememberingModel()),
                tools=[],
                checkpointer=checkpointer,
            )
            service = ResearchAgentService(cast(ResearchGraph, graph))
            try:
                first = await service.research_thread(
                    "Analyze Apple.",
                    thread_id=thread_id,
                    run_id=uuid4(),
                    checkpoint_id=None,
                )
                second = await service.research_thread(
                    "Now compare it with Microsoft.",
                    thread_id=thread_id,
                    run_id=uuid4(),
                    checkpoint_id=first.checkpoint_id,
                )

                assert second.answer == (
                    "Analyze Apple. | Now compare it with Microsoft."
                )
                assert second.checkpoint_id != first.checkpoint_id
            finally:
                await checkpointer.adelete_thread(str(thread_id))

    asyncio.run(
        exercise(),
        loop_factory=asyncio.SelectorEventLoop,
    )
