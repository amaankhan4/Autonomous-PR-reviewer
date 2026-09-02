"""Authentication endpoints: register, login, refresh, logout, me."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, client_ip, user_installation_ids
from app.core.config import settings
from app.core.enums import AuditAction
from app.core.errors import AuthenticationError, ConflictError
from app.core.logging import get_logger
from app.core.security import (
    create_token,
    create_token_pair,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.repository import Repository
from app.models.user import GitHubInstallation, InstallationMembership, User
from app.schemas.auth import (
    AuthContext,
    InstallationResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.common import Message
from app.services.audit import record_audit

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: DbSession) -> TokenResponse:
    existing = await db.execute(
        select(User).where(
            (func.lower(User.email) == payload.email.lower())
            | (func.lower(User.username) == payload.username.lower())
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            "An account with that email or username already exists",
            code="USER_ALREADY_EXISTS",
        )

    user = User(
        email=payload.email.lower(),
        username=payload.username,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    await record_audit(
        db,
        AuditAction.USER_REGISTERED,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    logger.info("auth.registered", user_id=user.id, username=user.username)
    return TokenResponse(**create_token_pair(user.id, {"username": user.username}))


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    result = await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    user = result.scalar_one_or_none()

    # Always run a hash comparison so a missing account and a wrong password
    # take a comparable amount of time.
    password_hash = user.password_hash if user else hash_password("invalid-placeholder")
    if not verify_password(payload.password, password_hash) or user is None:
        raise AuthenticationError("Incorrect email or password", code="INVALID_CREDENTIALS")
    if not user.is_active:
        raise AuthenticationError("This account is disabled", code="USER_DISABLED")

    await record_audit(
        db,
        AuditAction.USER_LOGIN,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return TokenResponse(**create_token_pair(user.id, {"username": user.username}))


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    claims = decode_token(payload.refresh_token, expected_type="refresh")
    user_id = str(claims.get("sub") or "")
    user = await db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise AuthenticationError("Refresh token is no longer valid", code="TOKEN_INVALID")

    return TokenResponse(
        access_token=create_token(user.id, "access", extra_claims={"username": user.username}),
        refresh_token=payload.refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout", response_model=Message)
async def logout(user: CurrentUser, request: Request, db: DbSession) -> Message:
    """Stateless logout.

    Tokens are short-lived and not server-side revocable in the MVP; the client
    discards them. The event is still audited so sign-outs are traceable.
    """
    await record_audit(
        db,
        AuditAction.USER_LOGOUT,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip_address=client_ip(request),
    )
    await db.commit()
    return Message(detail="Signed out. Discard the stored tokens.")


@router.get("/me", response_model=AuthContext)
async def read_me(user: CurrentUser, db: DbSession) -> AuthContext:
    installation_ids = await user_installation_ids(db, user)
    installations: list[InstallationResponse] = []
    if installation_ids:
        rows = await db.execute(
            select(
                GitHubInstallation,
                func.count(Repository.id).label("repository_count"),
            )
            .outerjoin(Repository, Repository.installation_id == GitHubInstallation.id)
            .where(GitHubInstallation.id.in_(installation_ids))
            .group_by(GitHubInstallation.id)
            .order_by(GitHubInstallation.created_at.asc())
        )
        for installation, repository_count in rows.all():
            item = InstallationResponse.model_validate(installation)
            item.repository_count = int(repository_count or 0)
            installations.append(item)

    return AuthContext(
        user=UserResponse.model_validate(user),
        installations=installations,
        demo_mode=settings.demo_mode,
    )


@router.get("/memberships", response_model=list[str])
async def list_membership_ids(user: CurrentUser, db: DbSession) -> list[str]:
    result = await db.execute(
        select(InstallationMembership.installation_id).where(
            InstallationMembership.user_id == user.id
        )
    )
    return [str(row) for row in result.scalars().all()]
