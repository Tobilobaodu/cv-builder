"""Multi-job-post coverage reporting endpoints — Sprint 5 / Product
Extension #2 (11-product-extensions.md §2).

POST /job-post-collections
GET  /job-post-collections
POST /job-post-collections/{collectionId}/coverage-report
GET  /coverage-reports/{reportId}

A pure read/aggregation layer over existing match_runs/
match_evidence_items — no new AI generation, no new evidence-binding
surface. Account-only throughout (no trial-session support), matching
03-data-model.md's schema exactly — a reasonably-scoped power-user
feature behind the account wall, not something a first-touch trial
identity needs.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import check_generation_rate_limit, get_client_key
from app.core.security import get_current_user, get_scoped_session_for_user, ownership_denied
from app.db.models import (
    AuditEvent,
    CoverageReport,
    CvFile,
    CvProfile,
    JobPost,
    JobPostCollection,
    ProcessingJob,
    User,
)
from app.schemas.coverage import (
    CoverageReportOut,
    CoverageReportTriggerRequest,
    CreateCollectionRequest,
    JobPostCollectionOut,
)
from app.schemas.jobs import ProcessingJobRef
from app.services.orchestration import enforce_concurrent_job_limit, mark_job_publish_failed
from app.workers.tasks import enqueue_coverage_report

router = APIRouter(tags=["product-extensions"])
logger = get_logger(__name__)


def _report_response(report: CoverageReport) -> CoverageReportOut:
    """Explicit construction, not response_model/model_validate
    auto-mapping — match_run_ids/aggregate_gaps are nullable columns
    (populated by the worker, absent until it runs) but the documented
    response contract always shows an array; coalescing None -> [] here
    keeps that true regardless of exactly when a report is read."""
    return CoverageReportOut(
        id=report.id,
        cv_profile_version_id=report.cv_profile_version_id,
        collection_id=report.collection_id,
        match_run_ids=report.match_run_ids or [],
        status=report.status,
        aggregate_gaps=report.aggregate_gaps or [],
        skipped_job_post_ids=report.skipped_job_post_ids,
        created_at=report.created_at,
        completed_at=report.completed_at,
    )


# ──────────────────────────────────────────────────────────────────────
# POST /job-post-collections
# ──────────────────────────────────────────────────────────────────────


@router.post("/job-post-collections", response_model=JobPostCollectionOut, status_code=201)
async def create_collection(
    body: CreateCollectionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Create a named collection of job posts for aggregate comparison.
    No rate limit — plain CRUD, no job dispatch, no LLM/matching call."""
    owned_result = await session.execute(
        select(JobPost.id).where(
            JobPost.id.in_(body.job_post_ids),
            JobPost.user_id == current_user.id,
        )
    )
    owned_ids = {row[0] for row in owned_result.all()}
    missing = [jp_id for jp_id in body.job_post_ids if jp_id not in owned_ids]
    if missing:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="job_post",
            entity_id=",".join(missing), detail=f"Job post(s) not found or not owned by you: {missing}",
        )

    collection = JobPostCollection(
        user_id=current_user.id,
        name=body.name,
        job_post_ids=body.job_post_ids,
    )
    session.add(collection)
    await session.commit()

    logger.info("job_post_collection_created", collection_id=collection.id, user_id=current_user.id)
    return JobPostCollectionOut.model_validate(collection)


# ──────────────────────────────────────────────────────────────────────
# GET /job-post-collections
# ──────────────────────────────────────────────────────────────────────


@router.get("/job-post-collections", response_model=list[JobPostCollectionOut])
async def list_collections(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    result = await session.execute(
        select(JobPostCollection).where(JobPostCollection.user_id == current_user.id)
        .order_by(JobPostCollection.created_at.desc())
        .limit(limit).offset(offset)
    )
    return [JobPostCollectionOut.model_validate(c) for c in result.scalars().all()]


# ──────────────────────────────────────────────────────────────────────
# POST /job-post-collections/{collectionId}/coverage-report
# ──────────────────────────────────────────────────────────────────────


@router.post(
    "/job-post-collections/{collectionId}/coverage-report",
    response_model=ProcessingJobRef,
    status_code=202,
)
async def trigger_coverage_report(
    collectionId: str,
    request: Request,
    body: CoverageReportTriggerRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Run an aggregated coverage-gap report for a CV against every job
    post in the collection. Rate-limited per client IP (generation tier)
    — job-creating, mirrors every other generation endpoint."""
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many coverage report requests. Please wait and try again.",
        )

    collection_result = await session.execute(
        select(JobPostCollection).where(
            JobPostCollection.id == collectionId,
            JobPostCollection.user_id == current_user.id,
        )
    )
    collection = collection_result.scalar_one_or_none()
    if collection is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="job_post_collection",
            entity_id=collectionId, detail="Collection not found",
        )

    # Resolve cvId -> current CvProfileVersion, same join pattern as
    # cover_letters.py::start_workflow — cvId is a CvFile id, not a
    # CvProfileVersion id directly.
    profile_result = await session.execute(
        select(CvProfile)
        .join(CvFile, CvFile.id == CvProfile.cv_file_id)
        .where(
            CvProfile.cv_file_id == body.cv_id,
            CvFile.user_id == current_user.id,
        )
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None or profile.current_version_id is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=body.cv_id, detail="No parsed CV profile found. Process a CV first.",
        )

    report = CoverageReport(
        user_id=current_user.id,
        cv_profile_version_id=profile.current_version_id,
        collection_id=collection.id,
        match_run_ids=[],
        aggregate_gaps=[],
        status="pending",
    )
    session.add(report)
    await session.flush()

    await enforce_concurrent_job_limit(session, user_id=current_user.id)

    proc_job = ProcessingJob(
        job_type="coverage_report",
        source_entity_type="coverage_report",
        source_entity_id=report.id,
        user_id=current_user.id,
        status="pending",
    )
    session.add(proc_job)

    session.add(AuditEvent(
        user_id=current_user.id,
        entity_type="coverage_report",
        entity_id=report.id,
        event_type="coverage_report_requested",
        actor_type="user",
    ))

    await session.commit()
    try:
        enqueue_coverage_report(proc_job.id)
    except Exception as e:
        mark_job_publish_failed(proc_job, "Failed to publish task to message broker.")
        await session.commit()
        logger.error("coverage_report_publish_failed", job_id=proc_job.id, error=str(e))
        raise

    logger.info("coverage_report_created", report_id=report.id, job_id=proc_job.id, collection_id=collection.id)
    return ProcessingJobRef(job_id=proc_job.id, status="queued")


# ──────────────────────────────────────────────────────────────────────
# GET /coverage-reports/{reportId}
# ──────────────────────────────────────────────────────────────────────


@router.get("/coverage-reports/{reportId}", response_model=CoverageReportOut)
async def get_coverage_report(
    reportId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    result = await session.execute(
        select(CoverageReport).where(
            CoverageReport.id == reportId,
            CoverageReport.user_id == current_user.id,
        )
    )
    report = result.scalar_one_or_none()
    if report is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="coverage_report",
            entity_id=reportId, detail="Coverage report not found",
        )
    return _report_response(report)
