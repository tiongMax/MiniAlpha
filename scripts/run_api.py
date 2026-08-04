"""Run MiniAlpha with an event loop compatible with async psycopg."""

import argparse
import asyncio
import selectors
import sys
from collections.abc import Sequence

import uvicorn


def postgres_compatible_loop_factory() -> asyncio.AbstractEventLoop:
    """Use a selector loop on Windows and the platform default elsewhere."""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop(selectors.SelectSelector())
    return asyncio.new_event_loop()


def build_parser() -> argparse.ArgumentParser:
    """Build the supported development-server command line."""
    parser = argparse.ArgumentParser(description="Run the MiniAlpha API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart the development server when source files change.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Start Uvicorn with the project-local event-loop factory."""
    args = build_parser().parse_args(argv)
    uvicorn.run(
        "app.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        loop="scripts.run_api:postgres_compatible_loop_factory",
    )


if __name__ == "__main__":
    main()
