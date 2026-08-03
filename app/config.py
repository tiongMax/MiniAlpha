"""Application configuration for the Phase 1 CLI."""

import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI


def create_model() -> ChatGoogleGenerativeAI:
    """Build the single Phase 1 model from environment configuration."""
    load_dotenv()

    model_name = (
        os.getenv("GEMINI_MODEL")
        or os.getenv("MODEL_NAME")
        or "gemini-2.5-flash"
    ).strip()
    api_key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or ""
    ).strip()

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
