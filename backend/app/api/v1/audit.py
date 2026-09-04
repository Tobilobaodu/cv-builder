"""Audit trail endpoint — GET /audit/{entityType}/{entityId}.

Documented in 05-openapi.yaml since the beginning but never implemented
(confirmed: no router registered in main.py, no audit.py module existed).
Workstream I's incident-response runbook needs a real endpoint to point at for
"which logs to check first", so this is built now rather than deferred.

IDOR-safe by construction: the query is scoped to AuditEvent.user_id ==
current_user.id, so a requester can only ever see audit events that name
themselves as the actor — never another user's entity trail (which returns an
empty list, not data). entityType is validated against an explicit allow-list
of the values this codebase actually writes to audit_events, never passed raw
into a query (per security-plan §7).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import get_current_user
from app.db import get_session
from app.db.models import AuditEvent, User
from app.schemas.audit import AuditEventResponse

router = APIRouter(tags=["audit"])
logger = get_logger(__name__)

# Explicit allow-list of the entity_type values this codebase actually writes
# to audit_events (grep-verified across app/api/v1/*.py). The openapi spec's
# enum (cv/job_post/match/...) predates the real entity_type vocabulary, so the
# allow-list follows the actual persisted values, not the stale spec enum.
_ALLOWED_ENTITY_TYPES = frozenset(
    {
        "cv_file",
        "job_post",
        "job_post_collection",
        "match_run",
        "processing_job",
        "tailored_cv_draft",
        "cover_letter_workflow",
        "cover_letter_draft",
        "export",
        "coverage_report",
        "trial_session",
        "user",
    }
)

# Denials are now audited too (event_type="access_denied", see
# app/core/security.py::ownership_denied) — bounded by real denial volume,
# not proportional to normal read traffic, but LIMIT/pagination is cheap
# insurance against a hot entity_id ever returning an unbounded result set.
_MAX_PAGE_SIZE = 200


@router.get("/audit/{entityType}/{entityId}", response_model=list[AuditEventResponse])
async def get_audit_events(
    entityType: str,
    entityId: str,
    limit: int = Query(default=_MAX_PAGE_SIZE, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Return the audit trail for an entity, scoped to the requester's own events."""
    if entityType not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown entity type '{entityType}'.",
        )

    result = await session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.entity_type == entityType,
            AuditEvent.entity_id == entityId,
            AuditEvent.user_id == current_user.id,
        )
        .order_by(AuditEvent.created_at)
        .limit(limit)
        .offset(offset)
    )
    events = result.scalars().all()

    return [
        AuditEventResponse(
            id=e.id,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            event_type=e.event_type,
            actor_type=e.actor_type,
            metadata=e.metadata_,
            created_at=e.created_at,
        )
        for e in events
    ]
