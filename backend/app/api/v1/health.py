"""Health, readiness and capability endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.core.config import settings
from app.integrations.github.factory import get_github_provider
from app.integrations.llm.factory import get_llm_provider
from app.schemas.common import HealthResponse
from app.services.queue import broker_reachable

router = APIRouter(tags=["system"])

VERSION = "0.1.0"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe: never touches a dependency, so it cannot flap."""
    return HealthResponse(
        status="ok",
        version=VERSION,
        environment=settings.ENVIRONMENT,
        demo_mode=settings.demo_mode,
        checks={},
        timestamp=datetime.now(UTC),
    )


@router.get("/ready", response_model=HealthResponse)
async def readiness(db: DbSession) -> HealthResponse:
    """Readiness probe: reports each dependency separately.

    A degraded broker or vector store does not make the API unready -- reviews
    fall back to in-process execution -- but a dead database does.
    """
    checks: dict[str, Any] = {}

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    checks["broker"] = {"ok": await broker_reachable(), "url_scheme": settings.broker_url.split("://")[0]}

    try:
        provider = get_github_provider()
        checks["github"] = {"ok": True, "provider": provider.name, "mock": provider.is_mock}
    except Exception as exc:
        checks["github"] = {"ok": False, "error": str(exc)[:200]}

    try:
        llm = get_llm_provider()
        checks["llm"] = {
            "ok": True,
            "provider": getattr(llm, "name", "unknown"),
            "model": getattr(llm, "model", "unknown"),
        }
    except Exception as exc:
        checks["llm"] = {"ok": False, "error": str(exc)[:200]}

    checks["vector_store"] = {"ok": True, "backend": settings.VECTOR_STORE}

    healthy = bool(checks["database"]["ok"])
    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=VERSION,
        environment=settings.ENVIRONMENT,
        demo_mode=settings.demo_mode,
        checks=checks,
        timestamp=datetime.now(UTC),
    )


@router.get("/config")
async def public_config() -> dict[str, Any]:
    """Non-sensitive configuration the frontend needs to render itself."""
    return {
        "app_name": settings.APP_NAME,
        "version": VERSION,
        "environment": settings.ENVIRONMENT,
        "demo_mode": settings.demo_mode,
        "mock_github": settings.MOCK_GITHUB,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_model": settings.LLM_MODEL,
        "vector_store": settings.VECTOR_STORE,
        "embedding_provider": settings.EMBEDDING_PROVIDER,
        "publish_reviews": settings.PUBLISH_REVIEWS,
        "default_review_event": settings.DEFAULT_REVIEW_EVENT,
        "enabled_analyzers": list(settings.ENABLED_ANALYZERS),
        "github_app_slug": settings.GITHUB_APP_SLUG,
        "demo_credentials": (
            {"email": settings.DEMO_USER_EMAIL, "password": settings.DEMO_USER_PASSWORD}
            if settings.SEED_DEMO_DATA and settings.demo_mode
            else None
        ),
    }
