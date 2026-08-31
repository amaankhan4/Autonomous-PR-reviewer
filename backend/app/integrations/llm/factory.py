"""LLM provider factory with graceful degradation to the mock reviewer."""

from __future__ import annotations

from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.integrations.llm.base import LLMProvider
from app.integrations.llm.mock_provider import MockLLMProvider

logger = get_logger(__name__)


def get_llm_provider(provider: str | None = None, model: str | None = None) -> LLMProvider:
    """Return the configured provider.

    If a real provider is requested but not usable (missing key), we fall back to
    the mock reviewer and log loudly rather than crashing the worker: an
    unreviewed PR is better than a dead queue, and the review record records
    which provider actually ran.
    """
    choice = (provider or settings.LLM_PROVIDER).lower()

    if choice == "openai":
        try:
            from app.integrations.llm.openai_provider import OpenAIProvider

            return OpenAIProvider(model=model)
        except LLMError as exc:
            logger.error("llm.provider_unavailable", provider=choice, error=str(exc))
            if settings.LLM_STRICT_PROVIDER:
                raise
            return MockLLMProvider()

    if choice == "gemini":
        try:
            from app.integrations.llm.gemini_provider import GeminiProvider

            return GeminiProvider(model=model)
        except LLMError as exc:
            logger.error("llm.provider_unavailable", provider=choice, error=str(exc))
            if settings.LLM_STRICT_PROVIDER:
                raise
            return MockLLMProvider()

    return MockLLMProvider(model=model)


__all__ = ["get_llm_provider"]
