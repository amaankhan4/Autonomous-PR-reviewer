"""API v1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    analytics,
    auth,
    findings,
    health,
    installations,
    pull_requests,
    repositories,
    reviews,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(installations.router)
api_router.include_router(repositories.router)
api_router.include_router(pull_requests.router)
api_router.include_router(reviews.router)
api_router.include_router(findings.router)
api_router.include_router(analytics.router)
api_router.include_router(webhooks.router)

__all__ = ["api_router"]
