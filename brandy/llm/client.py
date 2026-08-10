"""
LLM client wrapper.

Provides a unified interface for calling OpenAI-compatible models.
Lazy initialization — no crash on import if key is missing.
"""

import os
import logging

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client = None

# Strings that indicate the key has not been set (copied from .env.example)
_PLACEHOLDER_PATTERNS = {
    "your_key_here",
    "your-key-here",
    "sk-...",
    "openai_api_key",
    "placeholder",
    "",
}


def _is_valid_key(key: str | None) -> bool:
    """Return True only if the key looks like a real credential."""
    if not key:
        return False
    return key.strip().lower() not in _PLACEHOLDER_PATTERNS


def get_client():
    """
    Lazy-initialize and return the OpenAI client.
    Returns None (with a warning) if the key is missing, blank,
    or is still set to a placeholder value from .env.example.
    """
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("OPENAI_API_KEY")
    if not _is_valid_key(api_key):
        logger.warning(
            "OPENAI_API_KEY is missing or is a placeholder — "
            "LLM commentary will be skipped. "
            "Set a real key in .env to enable it."
        )
        return None

    try:
        from openai import OpenAI
        _client = OpenAI()
        logger.info("OpenAI client initialized")
        return _client
    except ImportError:
        logger.warning("openai package not installed — LLM features unavailable")
        return None


def chat_completion(
    prompt: str,
    system_message: str = "You are a helpful analyst.",
    model: str = "gpt-4.1-mini",
    temperature: float = 0.3,
    max_tokens: int = 500,
) -> str | None:
    """
    Send a chat completion request.
    Returns the response text, or None if LLM is unavailable.
    """
    client = get_client()
    if client is None:
        return None

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("LLM call failed")
        return None
