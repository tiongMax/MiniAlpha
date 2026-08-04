"""Tests for the supported API server launcher."""

import asyncio
import sys

from scripts import run_api


def test_windows_loop_factory_uses_selector(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    loop = run_api.postgres_compatible_loop_factory()

    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_non_windows_loop_factory_uses_platform_default(
    monkeypatch,
) -> None:
    sentinel_loop = object()

    def create_loop() -> object:
        return sentinel_loop

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(asyncio, "new_event_loop", create_loop)

    assert run_api.postgres_compatible_loop_factory() is sentinel_loop


def test_main_starts_uvicorn_with_supported_loop(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(app: str, **kwargs: object) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(run_api.uvicorn, "run", fake_run)

    run_api.main(["--host", "0.0.0.0", "--port", "9000", "--reload"])

    assert captured == {
        "app": "app.api.main:app",
        "host": "0.0.0.0",
        "port": 9000,
        "reload": True,
        "loop": "scripts.run_api:postgres_compatible_loop_factory",
    }
