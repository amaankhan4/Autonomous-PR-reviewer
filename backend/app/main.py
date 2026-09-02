"""FastAPI application factory.

Wires configuration, logging, middleware, error handling and the v1 router.
On startup it optionally creates tables and seeds demo data, so a single
``docker compose up`` (or ``uvicorn app.main:app``) yields a working product.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.v1.health import VERSION
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, get_engine, get_session_factory
from app.models.base import Base

logger = get_logger(__name__)

DESCRIPTION = """
Autonomous pull-request reviewer.

Deterministic analysis (diff, AST, static analysis, dependencies, tests,
security) is combined with repository-aware retrieval and an LLM that may only
reason over the supplied evidence. Every proposed finding is validated against
the real diff before it can be published.
"""


async def _create_schema() -> None:
    """Create tables directly.

    Used for SQLite/test/demo runs. Postgres deployments should run Alembic
    (``alembic upgrade head``); this is a convenience, not a replacement.
    """
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info(
        "app.starting",
        environment=settings.ENVIRONMENT,
        demo_mode=settings.demo_mode,
        llm_provider=settings.LLM_PROVIDER,
        vector_store=settings.VECTOR_STORE,
    )

    if settings.ENVIRONMENT in ("local", "test") or settings.DATABASE_URL.startswith("sqlite"):
        try:
            await _create_schema()
        except Exception as exc:  # pragma: no cover - startup resilience
            logger.error("app.schema_create_failed", error=str(exc))

    if settings.SEED_DEMO_DATA:
        try:
            from app.demo.seed import seed_demo_data

            factory = get_session_factory()
            async with factory() as session:
                await seed_demo_data(session)
        except Exception as exc:  # pragma: no cover - seeding must never block boot
            logger.error("app.seed_failed", error=str(exc))

    try:
        yield
    finally:
        logger.info("app.stopping")
        await dispose_engine()


def create_app() -> FastAPI:
    configure_logging()

    app = FastAPI(
        title=settings.APP_NAME,
        version=VERSION,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers below turn this into a clean JSON error;
            # here we only guarantee the timing/ID is still logged.
            logger.exception(
                "request.unhandled",
                method=request.method,
                path=request.url.path,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            structlog.contextvars.clear_contextvars()
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = str(duration_ms)
        if request.url.path not in ("/api/v1/health", "/health"):
            logger.info(
                "request.completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        structlog.contextvars.clear_contextvars()
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    @app.get("/health", include_in_schema=False)
    async def root_health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": VERSION})

    return app


app = create_app()
