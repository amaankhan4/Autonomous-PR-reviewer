"""Audit trail helper.

Security-relevant actions are recorded with the acting user, the entity touched
and a small structured context. Writes are best-effort: an audit failure must
never break the user-facing request, but it is logged loudly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import AuditAction
from app.core.logging import get_logger
from app.models.review import AuditLog

logger = get_logger(__name__)


async def record_audit(
    db: AsyncSession,
    action: AuditAction | str,
    *,
    user_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    context: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    try:
        db.add(
            AuditLog(
                user_id=user_id,
                action=str(action),
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                context=context or None,
                ip_address=ip_address,
                user_agent=(user_agent or None) and str(user_agent)[:400],
            )
        )
        await db.flush()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("audit.write_failed", action=str(action), error=str(exc))
