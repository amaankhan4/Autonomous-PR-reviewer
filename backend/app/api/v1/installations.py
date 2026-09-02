"""GitHub App installations: connect, list, sync, disconnect."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.api.deps import (
    AccessibleInstallations,
    CurrentUser,
    DbSession,
    client_ip,
    require_installation,
)
from app.core.config import settings
from app.core.enums import AuditAction
from app.core.errors import GitHubError, ValidationError
from app.core.logging import get_logger
from app.demo import fixtures
from app.integrations.github.factory import get_github_provider
from app.models.repository import Repository
from app.models.user import GitHubInstallation
from app.schemas.auth import InstallationResponse
from app.schemas.common import Message
from app.schemas.repository import InstallationSyncResponse
from app.services.audit import record_audit
from app.services.review_service import (
    grant_membership,
    sync_installation_repositories,
    upsert_installation,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/installations", tags=["installations"])


class ConnectInstallationRequest(BaseModel):
    installation_id: int | None = Field(
        default=None,
        ge=1,
        description="GitHub App installation id. Optional in demo mode.",
    )


@router.get("", response_model=list[InstallationResponse])
async def list_installations(
    db: DbSession, installation_ids: AccessibleInstallations
) -> list[InstallationResponse]:
    if not installation_ids:
        return []
    rows = await db.execute(
        select(GitHubInstallation, func.count(Repository.id))
        .outerjoin(Repository, Repository.installation_id == GitHubInstallation.id)
        .where(GitHubInstallation.id.in_(installation_ids))
        .group_by(GitHubInstallation.id)
        .order_by(GitHubInstallation.created_at.asc())
    )
    items: list[InstallationResponse] = []
    for installation, repository_count in rows.all():
        item = InstallationResponse.model_validate(installation)
        item.repository_count = int(repository_count or 0)
        items.append(item)
    return items


@router.post(
    "/connect", response_model=InstallationSyncResponse, status_code=status.HTTP_201_CREATED
)
async def connect_installation(
    payload: ConnectInstallationRequest,
    user: CurrentUser,
    request: Request,
    db: DbSession,
) -> InstallationSyncResponse:
    """Link a GitHub App installation to the signed-in user and mirror its repos.

    In demo mode the installation id is optional and resolves to the fixture
    account, so the product is fully explorable without any GitHub setup.
    """
    provider = get_github_provider()
    installation_id = payload.installation_id
    if installation_id is None:
        if not provider.is_mock:
            raise ValidationError(
                "installation_id is required when a real GitHub App is configured",
                code="INSTALLATION_ID_REQUIRED",
            )
        installation_id = fixtures.DEMO_INSTALLATION_ID

    try:
        info = await provider.get_installation(installation_id)
    except GitHubError:
        raise
    except Exception as exc:  # pragma: no cover - upstream variability
        raise GitHubError(f"Could not load installation {installation_id}: {exc}") from exc

    installation = await upsert_installation(db, info)
    await grant_membership(db, user, installation)
    total, added = await sync_installation_repositories(db, provider, installation)

    await record_audit(
        db,
        AuditAction.INSTALLATION_LINKED,
        user_id=user.id,
        entity_type="installation",
        entity_id=installation.id,
        context={"installation_id": installation_id, "repositories": total},
        ip_address=client_ip(request),
    )
    await db.commit()

    return InstallationSyncResponse(
        installation_id=installation_id,
        account_login=installation.account_login,
        repositories_synced=total,
        repositories_added=added,
        demo_mode=provider.is_mock,
    )


@router.post("/{installation_id}/sync", response_model=InstallationSyncResponse)
async def sync_installation(
    installation_id: str,
    user: CurrentUser,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> InstallationSyncResponse:
    installation = await require_installation(db, installation_id, installation_ids)
    provider = get_github_provider()
    total, added = await sync_installation_repositories(db, provider, installation)
    await db.commit()
    return InstallationSyncResponse(
        installation_id=installation.installation_id,
        account_login=installation.account_login,
        repositories_synced=total,
        repositories_added=added,
        demo_mode=provider.is_mock or settings.demo_mode,
    )


@router.delete("/{installation_id}", response_model=Message)
async def disconnect_installation(
    installation_id: str,
    user: CurrentUser,
    request: Request,
    db: DbSession,
    installation_ids: AccessibleInstallations,
) -> Message:
    """Remove the installation and everything derived from it."""
    installation = await require_installation(db, installation_id, installation_ids)
    await record_audit(
        db,
        AuditAction.INSTALLATION_REMOVED,
        user_id=user.id,
        entity_type="installation",
        entity_id=installation.id,
        context={"installation_id": installation.installation_id},
        ip_address=client_ip(request),
    )
    await db.execute(
        delete(GitHubInstallation).where(GitHubInstallation.id == installation.id)
    )
    await db.commit()
    return Message(detail="Installation disconnected and its data removed.")
