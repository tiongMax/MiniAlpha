"""Application-scoped PostgreSQL pool and LangGraph checkpointer lifecycle."""

from dataclasses import dataclass
from typing import cast

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from app.persistence.postgres import PostgresConversationRepository

_REQUIRED_TABLES = (
    "conversation_threads",
    "conversation_queries",
    "conversation_responses",
    "conversation_artifacts",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "semantic_research_cache",
)
_REQUIRED_ALEMBIC_REVISION = "004_point_2_semantic_cache"


async def _configure_connection(connection: AsyncConnection[DictRow]) -> None:
    """Configure pooled connections for checkpointer compatibility."""
    connection.prepare_threshold = 0


@dataclass(slots=True)
class PersistenceRuntime:
    """Resources shared by repositories and checkpointed graphs."""

    pool: AsyncConnectionPool[AsyncConnection[DictRow]]
    checkpointer: AsyncPostgresSaver
    repository: PostgresConversationRepository

    @classmethod
    async def open(
        cls,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> "PersistenceRuntime":
        """Open and verify one application-scoped PostgreSQL pool."""
        pool = cast(
            AsyncConnectionPool[AsyncConnection[DictRow]],
            AsyncConnectionPool(
                conninfo=database_url,
                min_size=min_size,
                max_size=max_size,
                open=False,
                configure=_configure_connection,
                check=AsyncConnectionPool.check_connection,
                kwargs={
                    "connect_timeout": 5,
                    "row_factory": dict_row,
                },
            ),
        )
        try:
            await pool.open(wait=True, timeout=10)
            runtime = cls(
                pool=pool,
                checkpointer=AsyncPostgresSaver(pool),
                repository=PostgresConversationRepository(pool),
            )
            if not await runtime.is_ready():
                raise RuntimeError(
                    "PostgreSQL is reachable but required tables are missing."
                )
            return runtime
        except Exception:
            await pool.close()
            raise

    async def is_ready(self) -> bool:
        """Check connectivity and all application/checkpoint tables."""
        if self.pool.closed:
            return False
        try:
            async with self.pool.connection() as connection:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT COUNT(*) AS table_count
                        FROM unnest(%s::text[]) AS required(table_name)
                        WHERE to_regclass('public.' || required.table_name)
                              IS NOT NULL
                        """,
                        (list(_REQUIRED_TABLES),),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        return False
                    table_count = row["table_count"]
                    if not (
                        isinstance(table_count, int)
                        and table_count == len(_REQUIRED_TABLES)
                    ):
                        return False
                    await cursor.execute("SELECT version_num FROM alembic_version")
                    revision = await cursor.fetchone()
                    return (
                        revision is not None
                        and revision["version_num"] == _REQUIRED_ALEMBIC_REVISION
                    )
        except Exception:
            return False

    async def close(self) -> None:
        """Close the owned PostgreSQL pool."""
        await self.pool.close()
