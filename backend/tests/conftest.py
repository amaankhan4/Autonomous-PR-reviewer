"""Shared test fixtures.

Environment is configured *before* any application import, because settings are
resolved once at import time. Tests run against a file-backed SQLite database
(so the API and the worker pipeline observe the same data through separate
connections) with the mock GitHub and mock LLM providers.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "pr_reviewer_tests.sqlite3"
if _TMP_DB.exists():
    _TMP_DB.unlink()

os.environ.update(
    ENVIRONMENT="test",
    DATABASE_URL=f"sqlite+aiosqlite:///{_TMP_DB.as_posix()}",
    SECRET_KEY="test-secret-key-not-for-production",
    GITHUB_WEBHOOK_SECRET="test-webhook-secret",
    MOCK_GITHUB="true",
    LLM_PROVIDER="mock",
    VECTOR_STORE="memory",
    EMBEDDING_PROVIDER="hashing",
    CELERY_TASK_ALWAYS_EAGER="true",
    SEED_DEMO_DATA="false",
    LOG_LEVEL="WARNING",
    STATIC_MYPY_ENABLED="false",
    ENABLE_STATIC_ANALYSIS="false",
    # A developer's `.env` may point at hosted Upstash services. Blank them so the
    # suite stays hermetic and never opens a network connection.
    REDIS_URL="redis://localhost:6379/0",
    UPSTASH_REDIS_REST_URL="",
    UPSTASH_REDIS_REST_TOKEN="",
    UPSTASH_VECTOR_REST_URL="",
    UPSTASH_VECTOR_REST_TOKEN="",
)

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import configure_engine, get_db  # noqa: E402
from app.demo import fixtures  # noqa: E402
from app.integrations.github.mock_provider import MockGitHubProvider  # noqa: E402
from app.integrations.vector.factory import set_vector_store  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.user import User  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine() -> AsyncIterator:
    test_engine = create_async_engine(
        os.environ["DATABASE_URL"], connect_args={"check_same_thread": False}
    )
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=test_engine, expire_on_commit=False, autoflush=False)
    configure_engine(test_engine, factory)
    try:
        yield test_engine
    finally:
        async with test_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()


@pytest.fixture
async def session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def db(session_factory) -> AsyncIterator:
    async with session_factory() as session:
        yield session


@pytest.fixture(autouse=True)
def reset_providers():
    """Keep provider singletons and in-memory state isolated between tests."""
    from app.integrations.vector.memory_store import InMemoryVectorStore

    MockGitHubProvider.reset()
    set_vector_store(InMemoryVectorStore(persist_path=None))
    yield
    MockGitHubProvider.reset()
    set_vector_store(None)


@pytest.fixture(autouse=True)
def no_background_dispatch(monkeypatch):
    """Make job dispatch observable and synchronous-by-choice.

    Tests assert on queued work explicitly instead of racing an asyncio task.
    """
    from app.services import queue as queue_module

    calls: list[tuple[str, tuple]] = []

    def fake_review(review_run_id: str):
        calls.append(("review", (review_run_id,)))
        return queue_module.Dispatch(task_id=None, mode="test", detail="queued (test)")

    def fake_index(repository_id: str, commit_sha=None, full=False):
        calls.append(("index", (repository_id, commit_sha, full)))
        return queue_module.Dispatch(task_id=None, mode="test", detail="queued (test)")

    monkeypatch.setattr(queue_module, "enqueue_review", fake_review)
    monkeypatch.setattr(queue_module, "enqueue_index", fake_index)
    for module_path in (
        "app.api.v1.reviews",
        "app.api.v1.pull_requests",
        "app.api.v1.webhooks",
        "app.api.v1.repositories",
    ):
        module = __import__(module_path, fromlist=["*"])
        if hasattr(module, "enqueue_review"):
            monkeypatch.setattr(module, "enqueue_review", fake_review)
        if hasattr(module, "enqueue_index"):
            monkeypatch.setattr(module, "enqueue_index", fake_index)
    return calls


@pytest.fixture
def dispatched(no_background_dispatch) -> list:
    return no_background_dispatch


@pytest.fixture
async def app(engine):
    # httpx's ASGITransport does not run lifespan events, so startup side
    # effects (schema creation, demo seeding) stay out of the tests.
    return create_app()


@pytest.fixture
async def client(app, session_factory) -> AsyncIterator[AsyncClient]:
    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


@pytest.fixture
async def user(db) -> User:
    account = User(
        email="reviewer@example.com",
        username="reviewer",
        full_name="Test Reviewer",
        password_hash=hash_password("Sup3rSecret!pass"),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@pytest.fixture
async def other_user(db) -> User:
    account = User(
        email="intruder@example.com",
        username="intruder",
        password_hash=hash_password("An0therSecret!pass"),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def token(client, user) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "Sup3rSecret!pass"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
async def other_token(client, other_user) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": other_user.email, "password": "An0therSecret!pass"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
async def connected(client, token) -> dict:
    """A signed-in user with the demo installation and repositories linked."""
    response = await client.post(
        "/api/v1/installations/connect",
        json={"installation_id": fixtures.DEMO_INSTALLATION_ID},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text
    repos = await client.get("/api/v1/repositories", headers=auth_headers(token))
    assert repos.status_code == 200, repos.text
    return {"installation": response.json(), "repositories": repos.json()["items"]}


@pytest.fixture
def payments_repo(connected) -> dict:
    for repo in connected["repositories"]:
        if repo["name"] == "payments-api":
            return repo
    raise AssertionError("demo payments-api repository was not created")
