"""Job post endpoints — POST /job-posts/url, POST /job-posts/text,
GET /job-posts, GET /job-posts/{jobPostId}, POST /job-posts/{jobPostId}/reprocess.

Matches 05-openapi.yaml. URL-based submissions are async via the queue
(SSRF-safe fetch → structuring); pasted text goes direct to structuring.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.core.rate_limit import (
    check_generation_rate_limit,
    check_url_fetch_rate_limit,
    get_client_key,
)
from app.services.orchestration import enforce_concurrent_job_limit, mark_job_publish_failed
from app.db.models import (
    AuditEvent,
    JobPost,
    JobPostProfile,
    ProcessingJob,
    User,
)
from app.core.security import (
    RequestIdentity,
    get_current_user,
    get_current_user_or_trial_session,
    get_scoped_session,
    get_scoped_session_for_user,
    identity_owner_filter,
    ownership_denied,
)
from app.workers.tasks import (
    enqueue_job_post_fetch,
    enqueue_job_post_parse,
)

router = APIRouter(tags=["job-posts"])
logger = get_logger(__name__)


# ── Pydantic schemas (inline for Phase 2 — move to schemas/ later) ───


class JobPostUrlRequest(BaseModel):
    url: str  # HttpUrl validates format; full SSRF check happens at fetch time


class JobPostTextRequest(BaseModel):
    text: str = Field(..., min_length=100, max_length=100_000)


class JobPostAccepted(BaseModel):
    """Returned when a job post is accepted for processing."""
    jobPostId: str
    processingJobId: str


class JobPostProfileOut(BaseModel):
    job_title: str | None = Field(None, alias="jobTitle")
    employer: str | None = None
    location: str | None = None
    required_skills: list[str] | None = Field(None, alias="requiredSkills")
    preferred_skills: list[str] | None = Field(None, alias="preferredSkills")
    responsibilities: list[str] | None = None
    qualifications: list[str] | None = None
    keywords: list[str] | None = None
    seniority: str | None = None
    confidence: float | None = None

    class Config:
        from_attributes = True
        populate_by_name = True


class JobPostResponse(BaseModel):
    id: str
    source_type: str = Field(alias="sourceType")
    source_url: str | None = Field(None, alias="sourceUrl")
    raw_text: str = Field(alias="rawText")
    status: str
    error_message: str | None = Field(None, alias="errorMessage")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    profile: JobPostProfileOut | None = None

    class Config:
        from_attributes = True
        populate_by_name = True
        populate_by_name = True


class JobPostListResponse(BaseModel):
    items: list[JobPostResponse]
    total: int
    limit: int
    offset: int


# ── Helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────
# POST /job-posts/url
# ──────────────────────────────────────────────────────────────────────


@router.post("/job-posts/url", response_model=JobPostAccepted, status_code=202)
async def submit_job_post_url(
    request: Request,
    body: JobPostUrlRequest,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Submit a job post URL for SSRF-safe fetching and structuring.

    Trial-accessible (Sprint 2). Rate-limited per client IP (url_fetch
    tier, see `10-security-plan.md` §9).
    """
    client_key = get_client_key(request)
    if not check_url_fetch_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many job post URL submissions. Please wait and try again.",
        )

    # Fast pre-check: URL must be parseable with http/https scheme.
    # Full SSRF validation (DNS/IP, redirect chain, timeout, size) happens
    # at fetch time in the worker — per 10-security-plan.md §4.
    from urllib.parse import urlparse

    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="Only http and https URLs are supported.",
        )

    # Create job_post row
    job_post = JobPost(
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        source_type="url",
        source_url=body.url,
        raw_text="",  # populated by fetch worker
        status="pending",
    )
    session.add(job_post)
    await session.flush()

    # Create processing job after concurrency check
    await enforce_concurrent_job_limit(session, user_id=identity.user_id, trial_session_id=identity.trial_session_id)

    proc_job = ProcessingJob(
        job_type="job_post_fetch",
        source_entity_type="job_post",
        source_entity_id=job_post.id,
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        status="pending",
    )
    session.add(proc_job)
    await session.flush()

    # Audit
    session.add(AuditEvent(
        user_id=identity.user_id,
        entity_type="job_post",
        entity_id=job_post.id,
        event_type="upload",  # reuse existing event type
        actor_type="user" if identity.user else "trial_session",
    ))

    await session.commit()
    # Enqueue the SSRF-safe fetch worker
    try:
        enqueue_job_post_fetch(proc_job.id)
    except Exception as e:
        mark_job_publish_failed(proc_job, 'Failed to publish task to message broker.')
        await session.commit()
        logger.error('job_post_publish_failed', job_id=proc_job.id, error=str(e))
        raise

    logger.info(
        "job_post_url_submitted",
        job_post_id=job_post.id,
        job_id=proc_job.id,
        url=body.url,
    )

    return JobPostAccepted(
        jobPostId=job_post.id,
        processingJobId=proc_job.id,
    )


# ──────────────────────────────────────────────────────────────────────
# POST /job-posts/text
# ──────────────────────────────────────────────────────────────────────


@router.post("/job-posts/text", response_model=JobPostAccepted, status_code=202)
async def submit_job_post_text(
    request: Request,
    body: JobPostTextRequest,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Submit pasted job post text for structuring.

    Trial-accessible (Sprint 2). Rate-limited per client IP (generation
    tier, see `10-security-plan.md` §9).
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many job post submissions. Please wait and try again.",
        )

    # Create job_post row with raw_text populated directly
    job_post = JobPost(
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        source_type="text",
        source_url=None,
        raw_text=body.text,
        status="pending",
    )
    session.add(job_post)
    await session.flush()

    # Create processing job — goes straight to parse
    await enforce_concurrent_job_limit(session, user_id=identity.user_id, trial_session_id=identity.trial_session_id)

    proc_job = ProcessingJob(
        job_type="job_post_parse",
        source_entity_type="job_post",
        source_entity_id=job_post.id,
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        status="pending",
    )
    session.add(proc_job)
    await session.flush()

    session.add(AuditEvent(
        user_id=identity.user_id,
        entity_type="job_post",
        entity_id=job_post.id,
        event_type="upload",
        actor_type="user" if identity.user else "trial_session",
    ))

    await session.commit()
    try:
        enqueue_job_post_parse(proc_job.id)
    except Exception as e:
        mark_job_publish_failed(proc_job, 'Failed to publish task to message broker.')
        await session.commit()
        logger.error('job_post_publish_failed', job_id=proc_job.id, error=str(e))
        raise

    logger.info(
        "job_post_text_submitted",
        job_post_id=job_post.id,
        job_id=proc_job.id,
    )

    return JobPostAccepted(
        jobPostId=job_post.id,
        processingJobId=proc_job.id,
    )


# ──────────────────────────────────────────────────────────────────────
# GET /job-posts
# ──────────────────────────────────────────────────────────────────────


@router.get("/job-posts", response_model=JobPostListResponse)
async def list_job_posts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """List job posts for the current user, with pagination."""
    query = select(JobPost).where(
        JobPost.user_id == current_user.id,
        JobPost.deleted_at.is_(None),
    )
    if status:
        query = query.where(JobPost.status == status)

    total_query = select(func.count()).select_from(JobPost).where(
        JobPost.user_id == current_user.id,
        JobPost.deleted_at.is_(None),
    )
    total = (await session.execute(total_query)).scalar() or 0

    items_query = (
        query.options(selectinload(JobPost.profile))
        .order_by(JobPost.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(items_query)
    rows = result.unique().scalars().all()

    items = [_job_post_to_response(r) for r in rows]

    return JobPostListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ──────────────────────────────────────────────────────────────────────
# GET /job-posts/{jobPostId}
# ──────────────────────────────────────────────────────────────────────


@router.get("/job-posts/{jobPostId}", response_model=JobPostResponse)
async def get_job_post(
    jobPostId: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Get a single job post with its structured profile (IDOR-safe).

    Trial-accessible (Sprint 2) — a trial session needs the structured
    profile to exist (status == "completed") before calling POST /matches.
    """
    result = await session.execute(
        select(JobPost)
        .options(selectinload(JobPost.profile))
        .where(
            JobPost.id == jobPostId,
            JobPost.deleted_at.is_(None),
            identity_owner_filter(JobPost, identity),
        )
    )
    job_post = result.unique().scalar_one_or_none()
    if job_post is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="job_post",
            entity_id=jobPostId, detail="Job post not found",
        )

    return _job_post_to_response(job_post)


# ──────────────────────────────────────────────────────────────────────
# DELETE /job-posts/{jobPostId}
# ──────────────────────────────────────────────────────────────────────


@router.delete("/job-posts/{jobPostId}", status_code=202)
async def delete_job_post(
    jobPostId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Delete a job post. Returns 404 if not owned by current user or already deleted."""
    result = await session.execute(
        select(JobPost).where(
            JobPost.id == jobPostId,
            JobPost.user_id == current_user.id,
            JobPost.deleted_at.is_(None),
        )
    )
    job_post = result.scalar_one_or_none()

    if job_post is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="job_post",
            entity_id=jobPostId, detail="Job post not found.",
        )

    job_post.deleted_at = func.now()

    session.add(
        AuditEvent(
            user_id=current_user.id,
            event_type="deletion_requested",
            entity_type="job_post",
            entity_id=job_post.id,
            actor_type="user",
        )
    )

    await session.commit()
    logger.info("job_post_deleted", job_post_id=jobPostId, user_id=current_user.id)


# ──────────────────────────────────────────────────────────────────────
# POST /job-posts/{jobPostId}/reprocess
# ──────────────────────────────────────────────────────────────────────


@router.post("/job-posts/{jobPostId}/reprocess", response_model=JobPostAccepted, status_code=202)
async def reprocess_job_post(
    request: Request,
    jobPostId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Re-run the structuring logic for an already-fetched job post.

    Does NOT re-fetch the URL — only re-parses existing raw_text.
    Rate-limited per client IP (generation tier).
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many job post submissions. Please wait and try again.",
        )

    result = await session.execute(
        select(JobPost).where(
            JobPost.id == jobPostId,
            JobPost.user_id == current_user.id,
        )
    )
    job_post = result.scalar_one_or_none()
    if job_post is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="job_post",
            entity_id=jobPostId, detail="Job post not found",
        )

    await enforce_concurrent_job_limit(session, current_user.id)

    proc_job = ProcessingJob(
        job_type="job_post_parse",
        source_entity_type="job_post",
        source_entity_id=job_post.id,
        user_id=current_user.id,
        status="pending",
    )
    session.add(proc_job)
    await session.commit()
    try:
        enqueue_job_post_parse(proc_job.id)
    except Exception as e:
        mark_job_publish_failed(proc_job, 'Failed to publish task to message broker.')
        await session.commit()
        logger.error('job_post_publish_failed', job_id=proc_job.id, error=str(e))
        raise

    return JobPostAccepted(
        jobPostId=job_post.id,
        processingJobId=proc_job.id,
    )


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _job_post_to_response(jp: JobPost) -> JobPostResponse:
    profile = None
    if jp.profile is not None:
        profile = JobPostProfileOut(
            job_title=jp.profile.job_title,
            employer=jp.profile.employer,
            location=jp.profile.location,
            required_skills=jp.profile.required_skills,
            preferred_skills=jp.profile.preferred_skills,
            responsibilities=jp.profile.responsibilities,
            qualifications=jp.profile.qualifications,
            keywords=jp.profile.keywords,
            seniority=jp.profile.seniority,
            confidence=jp.profile.confidence,
        )

    return JobPostResponse(
        id=jp.id,
        source_type=jp.source_type,
        source_url=jp.source_url,
        raw_text=jp.raw_text,
        status=jp.status,
        error_message=jp.error_message,
        created_at=jp.created_at.isoformat() if hasattr(jp.created_at, "isoformat") else str(jp.created_at),
        updated_at=jp.updated_at.isoformat() if hasattr(jp.updated_at, "isoformat") else str(jp.updated_at),
        profile=profile,
    )