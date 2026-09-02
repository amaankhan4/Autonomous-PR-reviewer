"""Demo data seeding.

Boots the product into a state that can be explored immediately: a demo user,
a linked (mock) installation, the fixture repositories and their pull requests.
Seeding is idempotent, so restarting the container never duplicates rows.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.demo import fixtures
from app.integrations.github.factory import get_github_provider
from app.models.user import User
from app.services.review_service import (
    grant_membership,
    sync_installation_repositories,
    upsert_installation,
    upsert_pull_request,
)

logger = get_logger(__name__)


async def seed_demo_data(db: AsyncSession) -> dict[str, str]:
    """Create (or refresh) the demo user, installation, repos and PRs."""
    result = await db.execute(
        select(User).where(func.lower(User.email) == settings.DEMO_USER_EMAIL.lower())
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            email=settings.DEMO_USER_EMAIL.lower(),
            username="demo",
            full_name="Demo Reviewer",
            password_hash=hash_password(settings.DEMO_USER_PASSWORD),
        )
        db.add(user)
        await db.flush()
        logger.info("demo.user_created", email=user.email)

    provider = get_github_provider()
    if not provider.is_mock:
        logger.info("demo.skipped_github_sync", reason="real GitHub provider configured")
        await db.commit()
        return {"user_id": user.id, "installation_id": "", "detail": "user only"}

    info = await provider.get_installation(fixtures.DEMO_INSTALLATION_ID)
    installation = await upsert_installation(db, info)
    await grant_membership(db, user, installation)
    total, added = await sync_installation_repositories(db, provider, installation)

    from app.models.repository import Repository

    for demo_pr in fixtures.DEMO_PULL_REQUESTS:
        repo_row = await db.execute(
            select(Repository).where(Repository.full_name == demo_pr.repo_full_name)
        )
        repository = repo_row.scalar_one_or_none()
        if repository is None:
            continue
        pr_info = await provider.get_pull_request(
            installation.installation_id, repository.full_name, demo_pr.number
        )
        await upsert_pull_request(db, repository, pr_info)

    await db.commit()
    logger.info(
        "demo.seeded",
        user=user.email,
        installation=installation.account_login,
        repositories=total,
        new_repositories=added,
        pull_requests=len(fixtures.DEMO_PULL_REQUESTS),
    )
    return {
        "user_id": user.id,
        "installation_id": installation.id,
        "detail": f"{total} repositories, {len(fixtures.DEMO_PULL_REQUESTS)} pull requests",
    }
