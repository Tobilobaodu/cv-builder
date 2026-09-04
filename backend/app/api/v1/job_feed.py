"""Free-API job feed endpoints (item 7).

GET  /job-feed                        — browse/search the shared catalog
GET  /job-feed/{feedPostingId}        — single listing
POST /job-feed/{feedPostingId}/import — pull a listing into the user's
                                         own job_posts for tailoring

feed_job_postings has no owner column (see its model docstring) — the GET
routes need no identity at all, just the general per-IP rate-limit tier
already used for other unauthenticated traffic, and a plain (unscoped)
session, since there's no RLS policy on this table to scope against.
Import is the boundary where shared catalog data becomes an owned
resource: it's exactly job_posts.py's POST /job-posts/text flow, sourced
from a feed row's description instead of user-typed text, so a listing
found this way goes through the *same* structuring/matching/tailoring
pipeline as any other job post — no separate code path downstream of
this endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import check_generation_rate_limit, check_rate_limit, get_client_key
from app.core.security import (
    RequestIdentity,
    get_current_user_or_trial_session,
    get_scoped_session,
)
from app.db.models import AuditEvent, FeedJobPosting, JobPost, ProcessingJob
from app.db.session import get_session
from app.schemas.job_feed import FeedImportAccepted, FeedJobPostingListResponse, FeedJobPostingOut
from app.services.orchestration import enforce_concurrent_job_limit, mark_job_publish_failed
from app.workers.tasks import enqueue_job_post_parse

router = APIRouter(tags=["job-feed"])
logger = get_logger(__name__)

# A listing's description must clear the same practical floor
# job_posts.py's POST /job-posts/text enforces (min_length=100) — some
# sources (Reed's search endpoint especially) return only a short
# snippet, which isn't enough for the structuring pipeline to work with.
_MIN_DESCRIPTION_LENGTH = 100


# ──────────────────────────────────────────────────────────────────────
# GET /job-feed
# ──────────────────────────────────────────────────────────────────────


@router.get("/job-feed", response_model=FeedJobPostingListResponse)
async def list_job_feed(
    request: Request,
    q: str | None = Query(None, max_length=200),
    location: str | None = Query(None, max_length=200),
    remote: bool | None = Query(None),
    source: str | None = Query(None, max_length=20),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """Browse/search the shared feed catalog. No auth required — this is
    read-only, unowned inventory (see FeedJobPosting's model docstring)."""
    client_key = get_client_key(request)
    if not check_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait and try again.",
        )

    filters = []
    if q:
        like = f"%{q}%"
        filters.append(or_(FeedJobPosting.title.ilike(like), FeedJobPosting.company.ilike(like)))
    if location:
        filters.append(FeedJobPosting.location.ilike(f"%{location}%"))
    if remote is not None:
        filters.append(FeedJobPosting.remote == remote)
    if source:
        filters.append(FeedJobPosting.source == source)

    total_result = await session.execute(
        select(func.count()).select_from(FeedJobPosting).where(*filters)
    )
    total = total_result.scalar() or 0

    items_result = await session.execute(
        select(FeedJobPosting)
        .where(*filters)
        .order_by(FeedJobPosting.posted_at.desc().nullslast(), FeedJobPosting.fetched_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items = items_result.scalars().all()

    return FeedJobPostingListResponse(
        items=[FeedJobPostingOut.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# ──────────────────────────────────────────────────────────────────────
# GET /job-feed/{feedPostingId}
# ──────────────────────────────────────────────────────────────────────


@router.get("/job-feed/{feedPostingId}", response_model=FeedJobPostingOut)
async def get_job_feed_posting(
    feedPostingId: str,
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(FeedJobPosting).where(FeedJobPosting.id == feedPostingId)
    )
    posting = result.scalar_one_or_none()
    if posting is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return FeedJobPostingOut.model_validate(posting)


# ──────────────────────────────────────────────────────────────────────
# POST /job-feed/{feedPostingId}/import
# ──────────────────────────────────────────────────────────────────────


@router.post("/job-feed/{feedPostingId}/import", response_model=FeedImportAccepted, status_code=202)
async def import_job_feed_posting(
    feedPostingId: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Create an owned JobPost from a feed listing and enqueue structuring
    — trial-accessible, same as job_posts.py's POST /job-posts/text, since
    finding a job via the feed and finding one by pasting text are the
    same product action from here on."""
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many job post submissions. Please wait and try again.",
        )

    # feed_job_postings isn't RLS-covered (no owner column) — read it on
    # the unscoped session route dependency chain would be the "correct"
    # shape, but get_scoped_session already gives us a working session
    # here and RLS simply never fires for a table with no policy, so a
    # second session isn't needed just to read this row.
    posting_result = await session.execute(
        select(FeedJobPosting).where(FeedJobPosting.id == feedPostingId)
    )
    posting = posting_result.scalar_one_or_none()
    if posting is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    if len(posting.description) < _MIN_DESCRIPTION_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="This listing's description is too short to structure. "
                   "Open the original posting and paste its full text instead.",
        )

    job_post = JobPost(
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        source_type="text",
        source_url=posting.url,
        raw_text=posting.description,
        status="pending",
    )
    session.add(job_post)
    await session.flush()

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
        mark_job_publish_failed(proc_job, "Failed to publish task to message broker.")
        await session.commit()
        logger.error("job_feed_import_publish_failed", job_id=proc_job.id, error=str(e))
        raise

    logger.info(
        "job_feed_posting_imported", feed_posting_id=feedPostingId,
        job_post_id=job_post.id, job_id=proc_job.id,
    )
    return FeedImportAccepted(jobPostId=job_post.id, processingJobId=proc_job.id)
