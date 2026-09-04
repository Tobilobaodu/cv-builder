"""Anonymous trial session claim logic (Sprint 2).

Separate from orchestration.py, which is scoped to creating processing
jobs and dispatching worker tasks — claiming a trial session is a data
reconciliation concern, not a job-orchestration one.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    CvFile,
    CvProfileVersion,
    JobPost,
    MatchRun,
    ProcessingJob,
    TrialSession,
)

logger = get_logger(__name__)


@dataclass
class ClaimTrialResult:
    cv_files_reassigned: int
    job_posts_reassigned: int
    match_runs_reassigned: int


async def claim_trial_session(
    session: AsyncSession, trial_session_id: str, user_id: str,
) -> ClaimTrialResult:
    """Reassign every row a trial session owns to a real user.

    Does NOT commit — the caller controls the transaction boundary. Unlike
    orchestration.py's create_processing_job(), nothing here is dispatched
    to a Celery worker, so there's no race to guard against by committing
    early: nothing is durable until the caller's own session.commit() runs,
    so a failure anywhere after this returns (e.g. while adding an audit
    event) leaves nothing reassigned once the caller's transaction rolls
    back.

    Raises HTTPException directly for not-found (404) / already-claimed
    or expired (409) — matching the precedent enforce_concurrent_job_limit
    already set for service-layer functions that need to reject before
    the route does anything else.
    """
    result = await session.execute(
        select(TrialSession).where(TrialSession.id == trial_session_id)
    )
    trial_session = result.scalar_one_or_none()
    if trial_session is None:
        raise HTTPException(status_code=404, detail="Trial session not found.")

    if trial_session.claimed_by_user_id is not None:
        raise HTTPException(status_code=409, detail="Trial session has already been claimed.")

    if trial_session.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="Trial session has expired.")

    cv_files_result = await session.execute(
        sa_update(CvFile)
        .where(CvFile.trial_session_id == trial_session.id)
        .values(user_id=user_id, trial_session_id=None)
    )
    await session.execute(
        sa_update(CvProfileVersion)
        .where(CvProfileVersion.trial_session_id == trial_session.id)
        .values(user_id=user_id, trial_session_id=None)
    )
    job_posts_result = await session.execute(
        sa_update(JobPost)
        .where(JobPost.trial_session_id == trial_session.id)
        .values(user_id=user_id, trial_session_id=None)
    )
    match_runs_result = await session.execute(
        sa_update(MatchRun)
        .where(MatchRun.trial_session_id == trial_session.id)
        .values(user_id=user_id, trial_session_id=None)
    )
    await session.execute(
        sa_update(ProcessingJob)
        .where(ProcessingJob.trial_session_id == trial_session.id)
        .values(user_id=user_id, trial_session_id=None)
    )

    trial_session.claimed_by_user_id = user_id
    trial_session.claimed_at = datetime.now(timezone.utc)

    logger.info(
        "trial_claimed",
        trial_session_id=trial_session_id,
        user_id=user_id,
        cv_files_reassigned=cv_files_result.rowcount,
        job_posts_reassigned=job_posts_result.rowcount,
        match_runs_reassigned=match_runs_result.rowcount,
    )

    return ClaimTrialResult(
        cv_files_reassigned=cv_files_result.rowcount,
        job_posts_reassigned=job_posts_result.rowcount,
        match_runs_reassigned=match_runs_result.rowcount,
    )
