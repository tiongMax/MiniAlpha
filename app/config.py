"""Application configuration for MiniAlpha entry points."""

import os
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from langchain_google_genai import ChatGoogleGenerativeAI


def get_database_url() -> str:
    """Return the PostgreSQL connection URL required by persistent threads.

    Raises:
        RuntimeError: If ``DATABASE_URL`` is absent or blank.
    """
    load_dotenv()
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "Missing required configuration: DATABASE_URL. "
            "Copy .env.example to .env and fill in the value."
        )
    return database_url


def get_redis_url() -> str:
    """Return the Redis URL required by replayable run events."""
    load_dotenv()
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError(
            "Missing required configuration: REDIS_URL. "
            "Copy .env.example to .env and fill in the value."
        )
    return redis_url


def get_positive_int(name: str, default: int) -> int:
    """Return a positive integer setting from the environment."""
    load_dotenv()
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


def get_timeout_seconds(name: str, default: float) -> float:
    """Return a positive timeout from the environment."""
    load_dotenv()
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number of seconds.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


def get_boolean(name: str, default: bool) -> bool:
    """Return a strict boolean environment setting."""
    load_dotenv()
    raw_value = os.getenv(name, str(default)).strip().casefold()
    if raw_value in {"1", "true", "yes", "on"}:
        return True
    if raw_value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean.")


def get_float(name: str, default: float) -> float:
    """Return a finite floating-point environment setting."""
    load_dotenv()
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number.") from error
    if not value == value or value in {float("inf"), float("-inf")}:
        raise RuntimeError(f"{name} must be finite.")
    return value


def create_model() -> "ChatGoogleGenerativeAI":
    """Create the Gemini chat model configured through environment variables.

    ``GEMINI_MODEL`` falls back to ``MODEL_NAME`` and then
    ``gemini-2.5-flash``. ``GEMINI_API_KEY`` falls back to
    ``GOOGLE_API_KEY``.

    Returns:
        A configured Gemini chat-model client.

    Raises:
        RuntimeError: If no Gemini API key is available.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    load_dotenv()

    model_name = (
        os.getenv("GEMINI_MODEL") or os.getenv("MODEL_NAME") or "gemini-2.5-flash"
    ).strip()
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()

    missing = [
        name
        for name, value in (
            ("GEMINI_MODEL", model_name),
            ("GEMINI_API_KEY", api_key),
        )
        if not value
    ]
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(
            f"Missing required configuration: {names}. "
            "Copy .env.example to .env and fill in the values."
        )

    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=api_key,
    )
