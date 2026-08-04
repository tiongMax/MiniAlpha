"""Optional PostgreSQL contract test for conversation persistence."""

import asyncio
import os
from typing import cast
from uuid import uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.persistence.postgres import PostgresConversationRepository

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not configured",
)
def test_postgres_repository_completes_and_replays_a_run() -> None:
    """Verify the psycopg repository against a migrated test database."""
    database_url = TEST_DATABASE_URL
    assert database_url is not None

    async def exercise() -> None:
        pool = cast(
            AsyncConnectionPool[AsyncConnection[DictRow]],
            AsyncConnectionPool(
                conninfo=database_url,
                min_size=1,
                max_size=2,
                open=False,
                kwargs={"row_factory": dict_row},
            ),
        )
        await pool.open()
        repository = PostgresConversationRepository(pool)
        thread_id = None
        try:
            request_key = uuid4()
            admission = await repository.admit_run(
                thread_id=None,
                message="Analyze Apple.",
                request_key=request_key,
            )
            thread_id = admission.run.thread_id
            completed = await repository.complete_run(
                admission.run.run_id,
                expected_checkpoint_id=None,
                checkpoint_id="checkpoint-integration",
                answer="Apple is profitable.",
                tool_calls=[],
                artifacts=[
                    {
                        "artifact_type": "company_overview",
                        "schema_version": 1,
                        "status": "ok",
                        "data": {"symbol": "AAPL"},
                    }
                ],
            )
            replay = await repository.admit_run(
                thread_id=thread_id,
                message="Analyze Apple.",
                request_key=request_key,
            )
            stored_turn = await repository.get_turn(admission.run.run_id)

            assert completed.run.status == "completed"
            assert replay.replayed is True
            assert replay.run.run_id == admission.run.run_id
            assert stored_turn is not None
            assert stored_turn.artifacts[0].data == {"symbol": "AAPL"}
        finally:
            if thread_id is not None:
                async with pool.connection() as connection:
                    await connection.execute(
                        """
                        DELETE FROM conversation_threads
                        WHERE conversation_thread_id = %s
                        """,
                        (thread_id,),
                    )
            await pool.close()

    asyncio.run(
        exercise(),
        loop_factory=asyncio.SelectorEventLoop,
    )
