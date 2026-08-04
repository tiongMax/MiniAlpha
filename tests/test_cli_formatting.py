"""Tests for provider-neutral CLI message formatting."""

from cli import _text_content


def test_extracts_text_from_gemini_content_blocks() -> None:
    """Verify that display text excludes Gemini signature metadata."""
    content = [
        {
            "type": "text",
            "text": "Apple has strong operating margins.",
            "extras": {"signature": "provider-metadata"},
        }
    ]

    assert _text_content(content) == "Apple has strong operating margins."


def test_preserves_plain_string_content() -> None:
    """Verify that plain string message content passes through unchanged."""
    assert _text_content("Plain tool result") == "Plain tool result"
