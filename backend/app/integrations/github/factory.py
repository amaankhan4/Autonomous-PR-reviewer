"""GitHub provider selection.

``MOCK_GITHUB=true`` (the default) yields the fixture-backed provider so the
product is runnable with zero credentials. Setting it to ``false`` requires a
configured GitHub App and returns the real client.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.github.app_provider import GitHubAppProvider
from app.integrations.github.base import GitHubProvider
from app.integrations.github.mock_provider import MockGitHubProvider

logger = get_logger(__name__)

_override: GitHubProvider | None = None


@lru_cache
def _cached_provider() -> GitHubProvider:
    if settings.MOCK_GITHUB:
        logger.info("github_provider_selected", provider="mock")
        return MockGitHubProvider()
    logger.info("github_provider_selected", provider="github-app")
    return GitHubAppProvider()


def get_github_provider() -> GitHubProvider:
    if _override is not None:
        return _override
    return _cached_provider()


def set_github_provider(provider: GitHubProvider | None) -> None:
    """Inject a provider (used by tests and the demo seeder)."""
    global _override
    _override = provider
    _cached_provider.cache_clear()
