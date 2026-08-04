"""Optional lifecycle test for the application-scoped PostgreSQL runtime."""

import asyncio
import os

import pytest

from app.persistence.runtime import PersistenceRuntime

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured",
)
def test_persistence_runtime_opens_verifies_and_closes() -> None:
    """Verify one pool serves repositories and LangGraph checkpoints."""
    database_url = TEST_DATABASE_URL
    assert database_url is not None

    async def exercise() -> None:
        runtime = await PersistenceRuntime.open(database_url)
        assert await runtime.is_ready() is True
        assert runtime.repository is not None
        assert runtime.checkpointer is not None
        await runtime.close()
        assert await runtime.is_ready() is False

    asyncio.run(
        exercise(),
        loop_factory=asyncio.SelectorEventLoop,
    )
