"""Application / response-rate tracking endpoints (D5, 6a/6b).

POST   /applications
GET    /applications
GET    /applications/stats
GET    /applications/{applicationId}
PATCH  /applications/{applicationId}/status
POST   /applications/{applicationId}/notes
DELETE /applications/{applicationId}

Account-only throughout (no trial-session support) — same treatment as
job-post-collections/coverage-reports: tracking a search over time isn't
a first-touch trial need. No LLM/matching/job dispatch anywhere in this
router — plain CRUD plus a validated status machine
(app/core/application_states.py), so nothing here is rate-limited beyond
the general per-IP tier already applied at the ASGI layer.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.application_states import (
    RESPONDED_STATUSES,
    ApplicationStatus,
    assert_transition,
)
from app.core.logging import get_logger
from app.core.security import get_current_user, get_scoped_session_for_user, ownership_denied
from app.db.models import Application, ApplicationEvent, JobPost, User
from app.schemas.application import (
    AddApplicationNoteRequest,
    ApplicationListItem,
    ApplicationListResponse,
    ApplicationOut,
    ApplicationStatsOut,
    CreateApplicationRequest,
    UpdateApplicationStatusRequest,
)

router = APIRouter(tags=["applications"])
logger = get_logger(__name__)


def _application_out(app_row: Application) -> ApplicationOut:
    return ApplicationOut.model_validate(app_row)


# ──────────────────────────────────────────────────────────────────────
# POST /applications
# ──────────────────────────────────────────────────────────────────────


@router.post("/applications", response_model=ApplicationOut, status_code=201)
async def create_application(
    body: CreateApplicationRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Log a new application. job_post_id/tailored_cv_draft_id/
    cover_letter_draft_id are optional provenance links — jobTitle/
    employer are required directly so an application logged for a role
    the user applied to outside JBS entirely is just as valid a record.
    """
    if body.job_post_id is not None:
        owned = await session.execute(
            select(JobPost.id).where(
                JobPost.id == body.job_post_id,
                JobPost.user_id == current_user.id,
            )
        )
        if owned.scalar_one_or_none() is None:
            raise await ownership_denied(
                session, user_id=current_user.id, entity_type="job_post",
                entity_id=body.job_post_id, detail="Job post not found or not owned by you.",
            )

    application = Application(
        user_id=current_user.id,
        job_post_id=body.job_post_id,
        tailored_cv_draft_id=body.tailored_cv_draft_id,
        cover_letter_draft_id=body.cover_letter_draft_id,
        job_title=body.job_title,
        employer=body.employer,
        status=ApplicationStatus.APPLIED.value,
        notes=body.notes,
    )
    if body.applied_at is not None:
        application.applied_at = body.applied_at
    session.add(application)
    await session.flush()

    session.add(ApplicationEvent(
        application_id=application.id,
        event_type="status_change",
        from_status=None,
        to_status=ApplicationStatus.APPLIED.value,
        actor_type="user",
    ))
    await session.commit()
    await session.refresh(application, attribute_names=["events"])

    logger.info("application_created", application_id=application.id, user_id=current_user.id)
    return _application_out(application)


# ──────────────────────────────────────────────────────────────────────
# GET /applications
# ──────────────────────────────────────────────────────────────────────


@router.get("/applications", response_model=ApplicationListResponse)
async def list_applications(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    filters = [Application.user_id == current_user.id, Application.deleted_at.is_(None)]
    if status:
        filters.append(Application.status == status)

    total_result = await session.execute(
        select(func.count()).select_from(Application).where(*filters)
    )
    total = total_result.scalar() or 0

    items_result = await session.execute(
        select(Application)
        .where(*filters)
        .order_by(Application.applied_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = items_result.scalars().all()

    return ApplicationListResponse(
        items=[ApplicationListItem.model_validate(a) for a in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# ──────────────────────────────────────────────────────────────────────
# GET /applications/stats
# ──────────────────────────────────────────────────────────────────────


@router.get("/applications/stats", response_model=ApplicationStatsOut)
async def get_application_stats(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Response-rate summary — the whole point of tracking applications
    per the product ask: not just a list, but "am I hearing back."
    """
    result = await session.execute(
        select(Application.status, func.count())
        .where(Application.user_id == current_user.id, Application.deleted_at.is_(None))
        .group_by(Application.status)
    )
    by_status: dict[str, int] = {status: count for status, count in result.all()}
    total = sum(by_status.values())

    responded = sum(
        count for status, count in by_status.items()
        if status in {s.value for s in RESPONDED_STATUSES}
    )
    response_rate = (responded / total) if total > 0 else None

    return ApplicationStatsOut(total=total, by_status=by_status, response_rate=response_rate)


# ──────────────────────────────────────────────────────────────────────
# GET /applications/{applicationId}
# ──────────────────────────────────────────────────────────────────────


@router.get("/applications/{applicationId}", response_model=ApplicationOut)
async def get_application(
    applicationId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    result = await session.execute(
        select(Application)
        .options(selectinload(Application.events))
        .where(
            Application.id == applicationId,
            Application.user_id == current_user.id,
            Application.deleted_at.is_(None),
        )
    )
    application = result.unique().scalar_one_or_none()
    if application is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="application",
            entity_id=applicationId, detail="Application not found",
        )
    return _application_out(application)


# ──────────────────────────────────────────────────────────────────────
# PATCH /applications/{applicationId}/status
# ──────────────────────────────────────────────────────────────────────


@router.patch("/applications/{applicationId}/status", response_model=ApplicationOut)
async def update_application_status(
    applicationId: str,
    body: UpdateApplicationStatusRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    result = await session.execute(
        select(Application)
        .options(selectinload(Application.events))
        .where(
            Application.id == applicationId,
            Application.user_id == current_user.id,
            Application.deleted_at.is_(None),
        )
    )
    application = result.unique().scalar_one_or_none()
    if application is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="application",
            entity_id=applicationId, detail="Application not found",
        )

    from_status = application.status
    try:
        assert_transition(from_status, body.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    application.status = body.status
    session.add(ApplicationEvent(
        application_id=application.id,
        event_type="status_change",
        from_status=from_status,
        to_status=body.status,
        note=body.note,
        actor_type="user",
    ))
    await session.commit()
    await session.refresh(application, attribute_names=["events"])

    logger.info(
        "application_status_changed", application_id=application.id,
        from_status=from_status, to_status=body.status, user_id=current_user.id,
    )
    return _application_out(application)


# ──────────────────────────────────────────────────────────────────────
# POST /applications/{applicationId}/notes
# ──────────────────────────────────────────────────────────────────────


@router.post("/applications/{applicationId}/notes", response_model=ApplicationOut)
async def add_application_note(
    applicationId: str,
    body: AddApplicationNoteRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Record a note without changing status — e.g. "recruiter called,
    no decision yet" mid-interview-loop, where nothing in the status
    machine actually changed."""
    result = await session.execute(
        select(Application)
        .options(selectinload(Application.events))
        .where(
            Application.id == applicationId,
            Application.user_id == current_user.id,
            Application.deleted_at.is_(None),
        )
    )
    application = result.unique().scalar_one_or_none()
    if application is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="application",
            entity_id=applicationId, detail="Application not found",
        )

    session.add(ApplicationEvent(
        application_id=application.id,
        event_type="note_added",
        note=body.note,
        actor_type="user",
    ))
    await session.commit()
    await session.refresh(application, attribute_names=["events"])
    return _application_out(application)


# ──────────────────────────────────────────────────────────────────────
# DELETE /applications/{applicationId}
# ──────────────────────────────────────────────────────────────────────


@router.delete("/applications/{applicationId}", status_code=202)
async def delete_application(
    applicationId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    result = await session.execute(
        select(Application).where(
            Application.id == applicationId,
            Application.user_id == current_user.id,
            Application.deleted_at.is_(None),
        )
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="application",
            entity_id=applicationId, detail="Application not found",
        )

    application.deleted_at = func.now()
    await session.commit()
    logger.info("application_deleted", application_id=applicationId, user_id=current_user.id)
