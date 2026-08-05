"""Tests for application configuration boundaries."""

import asyncio

import pytest

from app import config
from scripts import setup_database as database_setup


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify persistent composition fails clearly without PostgreSQL."""
    monkeypatch.setattr(config, "load_dotenv", lambda: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        config.get_database_url()


def test_database_url_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify surrounding environment whitespace is not retained."""
    monkeypatch.setattr(config, "load_dotenv", lambda: False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "  postgresql://postgres:postgres@localhost/minialpha  ",
    )
    assert (
        config.get_database_url()
        == "postgresql://postgres:postgres@localhost/minialpha"
    )


def test_redis_url_is_required_and_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify production event transport requires an explicit Redis URL."""
    monkeypatch.setattr(config, "load_dotenv", lambda: False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        config.get_redis_url()

    monkeypatch.setenv("REDIS_URL", "  redis://localhost:6379/0  ")
    assert config.get_redis_url() == "redis://localhost:6379/0"


def test_positive_integer_configuration_is_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda: False)
    monkeypatch.setenv("RUN_EVENT_RETENTION_SECONDS", "0")

    with pytest.raises(RuntimeError, match="greater than zero"):
        config.get_positive_int("RUN_EVENT_RETENTION_SECONDS", 86_400)


def test_database_setup_uses_selector_loop_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify psycopg setup avoids Windows' unsupported Proactor loop."""
    captured: dict[str, object] = {}

    def fake_run(coroutine: object, *, loop_factory: object) -> None:
        captured["loop_factory"] = loop_factory
        coroutine.close()  # type: ignore[attr-defined]

    monkeypatch.setattr(database_setup.sys, "platform", "win32")
    monkeypatch.setattr(database_setup.asyncio, "run", fake_run)

    database_setup.run_setup()

    loop_factory = captured["loop_factory"]
    assert callable(loop_factory)
    loop = loop_factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()
