"""Initialize MiniAlpha application and LangGraph checkpoint tables."""

import asyncio
import selectors
import sys
from collections.abc import Callable

from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config import get_database_url


async def setup_database() -> None:
    """Apply application migrations and initialize LangGraph tables."""
    command.upgrade(Config("alembic.ini"), "head")

    database_url = get_database_url()
    async with AsyncPostgresSaver.from_conn_string(database_url) as checkpointer:
        await checkpointer.setup()


def _create_selector_event_loop() -> asyncio.AbstractEventLoop:
    """Create the Windows event loop supported by psycopg async."""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def run_setup() -> None:
    """Run setup with an event loop compatible with psycopg on Windows."""
    loop_factory: Callable[[], asyncio.AbstractEventLoop] | None = None
    if sys.platform == "win32":
        loop_factory = _create_selector_event_loop
    asyncio.run(setup_database(), loop_factory=loop_factory)


if __name__ == "__main__":
    run_setup()
