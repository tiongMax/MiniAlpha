"""Shared deterministic helpers for FastAPI contract tests."""

import asyncio
from typing import cast

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from app.services.research_agent import ResearchAgentService, ResearchGraph


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
