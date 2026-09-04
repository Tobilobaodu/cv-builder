"""Orchestration service — creates processing jobs and enqueues worker tasks.

This is the synchronous handoff point from the API to the async queue.
API endpoints call these functions to persist job records and dispatch
tasks to Celery workers.
"""

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import ProcessingJob, TrialSession, User
from fastapi import HTTPException, status as fastapi_status
from app.core.config import settings
from app.core.job_states import ProcessingStatus, transition_job_status
from app.workers.tasks import (
    enqueue_text_extract,
    enqueue_ats_check,
    enqueue_cv_analyze,
)
from datetime import datetime, timezone
from app.core.logging import get_logger

logger = get_logger(__name__)


_ACTIVE_JOB_STATUSES = frozenset({'pending', 'queued', 'processing', 'retrying'})


def compute_task_key(job_type: str, source_entity_id: str, owner_id: str) -> str:
    """Idempotency key for ProcessingJob dedup — see task_key on the model
    and migration 013. Deterministic per (job_type, entity, owner) so a
    retried client request for the exact same operation resolves to the
    same key, letting the DB's partial unique index (or an app-level
    lookup first) find the in-flight job instead of starting a duplicate
    real worker task."""
    raw = f"{job_type}:{source_entity_id}:{owner_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def find_active_job_by_task_key(session: AsyncSession, task_key: str) -> ProcessingJob | None:
    """Look up an in-flight job for a task_key before creating a new one —
    used by create_processing_job (the upload/ATS-check pipeline). Not used
    by the generation endpoints (tailored_cvs.py, cover_letters.py), which
    build ProcessingJob rows directly and deliberately don't opt into
    task_key dedup — see the task_key column comment on ProcessingJob."""
    result = await session.execute(
        select(ProcessingJob).where(
            ProcessingJob.task_key == task_key,
            ProcessingJob.status.in_(_ACTIVE_JOB_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def enforce_concurrent_job_limit(
    session, user_id: str | None = None, trial_session_id: str | None = None,
) -> None:
    """Enforce the per-identity concurrent-job cap, for a real user or a
    trial session — exactly one, never both (Sprint 2 extends this from
    user-only). Locks the owning row (User or TrialSession) with FOR
    UPDATE before counting, so two concurrent requests from the same
    identity can't both read "under limit" and both proceed — see
    test_job_concurrency_limit.py for the race test this closes.
    """
    if (user_id is None) == (trial_session_id is None):
        raise ValueError(
            "enforce_concurrent_job_limit requires exactly one of user_id/trial_session_id"
        )

    limit = settings.max_concurrent_jobs_per_user

    if user_id is not None:
        lock = await session.execute(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        lock.all()
        owner_filter = ProcessingJob.user_id == user_id
    else:
        lock = await session.execute(
            select(TrialSession.id).where(TrialSession.id == trial_session_id).with_for_update()
        )
        lock.all()
        owner_filter = ProcessingJob.trial_session_id == trial_session_id

    active = (await session.execute(
        select(func.count()).select_from(ProcessingJob).where(
            owner_filter,
            ProcessingJob.status.in_(_ACTIVE_JOB_STATUSES),
        )
    )).scalar_one()
    if active >= limit:
        raise HTTPException(
            fastapi_status.HTTP_429_TOO_MANY_REQUESTS,
            detail='Too many active processing jobs. Wait for an existing job to finish, then try again.',
        )


async def create_processing_job(
    session: AsyncSession,
    job_type: str,
    source_entity_type: str,
    source_entity_id: str,
    user_id: str | None = None,
    trial_session_id: str | None = None,
) -> ProcessingJob:
    """Create a processing_jobs row and enqueue the corresponding Celery task.

    Exactly one of user_id/trial_session_id must be set — matches the
    ck_processing_jobs_exactly_one_owner DB constraint; this check exists
    so a caller bug surfaces here, not as an opaque constraint violation
    mid-transaction.

    Commits before dispatching to Celery, matching the commit-then-enqueue
    pattern every other job-creating route in this codebase already uses
    (job_posts.py, matches.py, tailored_cvs.py). Previously this flushed
    but did not commit before enqueuing: a worker could receive the task
    and query for the job row before this transaction had actually
    committed, find nothing, log job_not_found, and return — silently
    stranding the job at status='queued' forever with no retry. Confirmed
    live via worker_docling logs (job_not_found firing ~2-4ms after the
    row was flushed) while building Sprint 3's e2e tests.

    Returns the job row so the API can return the job_id immediately.
    """
    if (user_id is None) == (trial_session_id is None):
        raise ValueError(
            "create_processing_job requires exactly one of user_id/trial_session_id"
        )

    task_key = compute_task_key(job_type, source_entity_id, user_id or trial_session_id)
    existing = await find_active_job_by_task_key(session, task_key)
    if existing is not None:
        logger.info("processing_job_deduped", job_id=str(existing.id), job_type=job_type, task_key=task_key)
        return existing

    await enforce_concurrent_job_limit(session, user_id=user_id, trial_session_id=trial_session_id)

    job = ProcessingJob(
        job_type=job_type,
        source_entity_type=source_entity_type,
        source_entity_id=source_entity_id,
        user_id=user_id,
        trial_session_id=trial_session_id,
        status="queued",
        task_key=task_key,
    )
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        # Lost a race against a concurrent request for the same task_key —
        # the partial unique index caught what the SELECT above couldn't.
        # The other request's row is now the real job; use it instead of
        # raising a 500 for what is, from the client's perspective, a
        # successful (deduped) request.
        await session.rollback()
        existing = await find_active_job_by_task_key(session, task_key)
        if existing is not None:
            return existing
        raise

    try:
        # Dispatch to the correct Celery queue based on job_type
        if job_type == "text_extract":
            enqueue_text_extract(str(job.id))
        elif job_type == "ats_check":
            enqueue_ats_check(str(job.id))
        elif job_type == "cv_analyze":
            enqueue_cv_analyze(str(job.id))
        else:
            logger.warning("unknown_job_type_not_enqueued", job_type=job_type)
            transition_job_status(job, ProcessingStatus.FAILED, error=f"Unknown job type: {job_type}")
            await session.commit()
            return job
    except Exception as e:
        mark_job_publish_failed(job, "Failed to publish task to message broker.")
        await session.commit()
        logger.error("job_publish_failed", job_id=str(job.id), job_type=job_type, error=str(e))
        raise

    logger.info(
        "job_created",
        job_id=str(job.id),
        job_type=job_type,
        source_entity_type=source_entity_type,
        source_entity_id=source_entity_id,
    )

    return job


def mark_job_publish_failed(job: ProcessingJob, error: str) -> None:
    """Set the standard publish-failure terminal state on a job row.

    Called by every route that uses commit-then-enqueue when the broker
    publish fails, so the persisted job does not permanently occupy an
    active concurrency slot.
    """
    transition_job_status(job, ProcessingStatus.FAILED, error=error)
    job.failed_at = datetime.now(timezone.utc)


async def start_extraction_pipeline(
    session: AsyncSession,
    cv_file_id: str,
    user_id: str | None = None,
    trial_session_id: str | None = None,
) -> ProcessingJob:
    """Kick off the Docling → Textract → merge pipeline for a newly uploaded CV.

    Creates the first (and now only) extraction job (text_extract) — steps 3-6
    the Textract and merge jobs on completion. Accepts either a real user
    or a trial session (Sprint 2) — exactly one, per create_processing_job.
    """
    return await create_processing_job(
        session=session,
        job_type="text_extract",
        source_entity_type="cv_file",
        source_entity_id=cv_file_id,
        user_id=user_id,
        trial_session_id=trial_session_id,
    )