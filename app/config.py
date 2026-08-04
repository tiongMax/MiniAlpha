"""Application configuration for MiniAlpha entry points."""

import os
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    from langchain_google_genai import ChatGoogleGenerativeAI


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
