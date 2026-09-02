"""Repository endpoints: listing, detail, settings and re-indexing."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from app.api.deps import (
    AccessibleInstallations,
    CurrentUser,
    DbSession,
    OwnedRepository,
    Pagination,
    client_ip,
)
from app.core.enums import AuditAction, FindingStatus, IndexStatus, PullRequestState
from app.core.logging import get_logger
from app.models.pull_request import PullRequest
from app.models.repository import IndexedFile, Repository, RepositoryIndex
from app.models.review import Finding, ReviewRun
from app.schemas.common import Message, Page
from app.schemas.repository import (
    ReindexRequest,
    RepositoryDetail,
    RepositoryIndexResponse,
    RepositoryResponse,
    RepositorySettingsResponse,
    RepositorySettingsUpdate,
)
from app.services.audit import record_audit
from app.services.queue import enqueue_index
from app.services.review_service import ensure_repository_settings

logger = get_logger(__name__)
router = APIRouter(prefix="/repositories", tags=["repositories"])


@router.get("", response_model=Page[RepositoryResponse])
async def list_repositories(
    db: DbSession,
    installation_ids: AccessibleInstallations,
    pagination: Pagination,
    search: str | None = Query(default=None, max_length=200),
    is_active: bool | None = Query(default=None),
    installation_id: str | None = Query(default=None),
) -> Page[RepositoryResponse]:
    if not installation_ids:
        return Page.build([], 0, pagination.page, pagination.page_size)

    conditions = [Repository.installation_id.in_(installation_ids)]
    if installation_id:
        conditions.append(Repository.installation_id == installation_id)
    if is_active is not None:
        conditions.append(Repository.is_active.is_(is_active))
    if search:
        pattern = f"%{search.lower()}%"
        conditions.append(
            or_(
                func.lower(Repository.full_name).like(pattern),
                func.lower(Repository.description).like(pattern),
            )
        )

    total = await db.scalar(select(func.count(Repository.id)).where(*conditions)) or 0
    rows = await db.execute(
        select(Repository)
        .where(*conditions)
        .order_by(Repository.full_name.asc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    items = [RepositoryResponse.model_validate(row) for row in rows.scalars().all()]
    return Page.build(items, int(total), pagination.page, pagination.page_size)


@router.get("/{repository_id}", response_model=RepositoryDetail)
async def get_repository(repository: OwnedRepository, db: DbSession) -> RepositoryDetail:
    config = await ensure_repository_settings(db, repository)

    open_prs = await db.scalar(
        select(func.count(PullRequest.id)).where(
            PullRequest.repository_id == repository.id,
            PullRequest.state == PullRequestState.OPEN.value,
        )
    )
    total_reviews = await db.scalar(
        select(func.count(ReviewRun.id))
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .where(PullRequest.repository_id == repository.id)
    )
    open_findings = await db.scalar(
        select(func.count(Finding.id))
        .join(ReviewRun, ReviewRun.id == Finding.review_run_id)
        .join(PullRequest, PullRequest.id == ReviewRun.pull_request_id)
        .where(
            PullRequest.repository_id == repository.id,
            Finding.status == FindingStatus.OPEN.value,
        )
    )
    indexed_files = await db.scalar(
        select(func.count(IndexedFile.id)).where(IndexedFile.repository_id == repository.id)
    )
    last_index = await db.scalar(
        select(RepositoryIndex)
        .where(RepositoryIndex.repository_id == repository.id)
        .order_by(RepositoryIndex.created_at.desc())
        .limit(1)
    )

    detail = RepositoryDetail.model_validate(repository)
    detail.settings = RepositorySettingsResponse.model_validate(config)
    detail.open_pull_requests = int(open_prs or 0)
    detail.total_reviews = int(total_reviews or 0)
    detail.open_findings = int(open_findings or 0)
    detail.indexed_files = int(indexed_files or 0)
    if last_index is not None:
        detail.index_status = last_index.status
        detail.last_indexed_at = last_index.completed_at or last_index.created_at
    return detail


@router.get("/{repository_id}/settings", response_model=RepositorySettingsResponse)
async def get_repository_settings(
    repository: OwnedRepository, db: DbSession
) -> RepositorySettingsResponse:
    config = await ensure_repository_settings(db, repository)
    return RepositorySettingsResponse.model_validate(config)


@router.patch("/{repository_id}/settings", response_model=RepositorySettingsResponse)
async def update_repository_settings(
    payload: RepositorySettingsUpdate,
    repository: OwnedRepository,
    user: CurrentUser,
    request: Request,
    db: DbSession,
) -> RepositorySettingsResponse:
    config = await ensure_repository_settings(db, repository)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(config, field, value)
    await db.flush()

    await record_audit(
        db,
        AuditAction.SETTINGS_UPDATED,
        user_id=user.id,
        entity_type="repository",
        entity_id=repository.id,
        context={"changed": sorted(changes)},
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(config)
    return RepositorySettingsResponse.model_validate(config)


@router.post("/{repository_id}/activate", response_model=RepositoryResponse)
async def set_repository_active(
    repository: OwnedRepository,
    user: CurrentUser,
    db: DbSession,
    active: bool = Query(default=True),
) -> RepositoryResponse:
    repository.is_active = active
    await record_audit(
        db,
        AuditAction.REPOSITORY_ACTIVATED if active else AuditAction.REPOSITORY_DEACTIVATED,
        user_id=user.id,
        entity_type="repository",
        entity_id=repository.id,
    )
    await db.commit()
    await db.refresh(repository)
    return RepositoryResponse.model_validate(repository)


@router.post("/{repository_id}/reindex", response_model=Message)
async def reindex_repository(
    payload: ReindexRequest,
    repository: OwnedRepository,
    user: CurrentUser,
    db: DbSession,
) -> Message:
    await record_audit(
        db,
        AuditAction.REPOSITORY_REINDEXED,
        user_id=user.id,
        entity_type="repository",
        entity_id=repository.id,
        context={"full": payload.full, "ref": payload.ref},
    )
    await db.commit()

    dispatch = enqueue_index(repository.id, payload.ref, payload.full)
    return Message(
        detail=(
            f"{'Full' if payload.full else 'Incremental'} re-index of "
            f"{repository.full_name}: {dispatch.detail}."
        )
    )


@router.get("/{repository_id}/indexes", response_model=Page[RepositoryIndexResponse])
async def list_repository_indexes(
    repository: OwnedRepository, db: DbSession, pagination: Pagination
) -> Page[RepositoryIndexResponse]:
    total = await db.scalar(
        select(func.count(RepositoryIndex.id)).where(
            RepositoryIndex.repository_id == repository.id
        )
    )
    rows = await db.execute(
        select(RepositoryIndex)
        .where(RepositoryIndex.repository_id == repository.id)
        .order_by(RepositoryIndex.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    items = [RepositoryIndexResponse.model_validate(row) for row in rows.scalars().all()]
    return Page.build(items, int(total or 0), pagination.page, pagination.page_size)


@router.get("/{repository_id}/index-status", response_model=dict)
async def repository_index_status(repository: OwnedRepository, db: DbSession) -> dict:
    last = await db.scalar(
        select(RepositoryIndex)
        .where(RepositoryIndex.repository_id == repository.id)
        .order_by(RepositoryIndex.created_at.desc())
        .limit(1)
    )
    files = await db.scalar(
        select(func.count(IndexedFile.id)).where(IndexedFile.repository_id == repository.id)
    )
    chunks = await db.scalar(
        select(func.coalesce(func.sum(IndexedFile.chunk_count), 0)).where(
            IndexedFile.repository_id == repository.id
        )
    )
    return {
        "repository_id": repository.id,
        "status": last.status if last else IndexStatus.PENDING.value,
        "commit_sha": last.commit_sha if last else None,
        "indexed_files": int(files or 0),
        "indexed_chunks": int(chunks or 0),
        "last_run_at": (last.completed_at or last.created_at).isoformat() if last else None,
        "error_message": last.error_message if last else None,
    }
