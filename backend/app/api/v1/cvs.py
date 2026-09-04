"""CV endpoints — POST /cvs (upload), GET /cvs, GET/DELETE /cvs/{cvId}, GET /jobs/{jobId}.

Matches 05-openapi.yaml. All heavy work (extraction, parsing) is async via
the queue — these endpoints only accept/validate/persist and return immediately.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import (
    check_generation_rate_limit,
    check_upload_rate_limit,
    get_client_key,
)
from app.core.storage import generate_storage_key, upload_file
from app.db.models import (
    AtsReadinessCheck,
    AuditEvent,
    CvAnalysis,
    CvFile,
    CvExtractionPass,
    CvProfile,
    CvProfileVersion,
    CvRawText,
    ProcessingJob,
    User,
)
from app.schemas.cv import (
    AtsReadinessCheckResponse,
    CvAnalysisResponse,
    CvUploadAccepted,
    CvFileResponse,
    CvListResponse,
    CvExtractionDetailResponse,
    CvExtractionPassResponse,
    CvIssueItem,
    CvRawTextResponse,
    StructuralValidationResult,
)
from app.schemas.jobs import ProcessingJobResponse
from app.services.file_validation import validate_file_type, validate_file_size
from app.services.orchestration import start_extraction_pipeline, create_processing_job
from app.core.security import (
    RequestIdentity,
    get_current_user,
    get_current_user_or_trial_session,
    get_scoped_session,
    get_scoped_session_for_user,
    identity_owner_filter,
    ownership_denied,
)

router = APIRouter(tags=["cvs"])
logger = get_logger(__name__)


def _active_cv_query(user_id: str):
    """Base query for non-deleted CVs owned by the given user."""
    return select(CvFile).where(
        CvFile.user_id == user_id,
        CvFile.deleted_at.is_(None),
    )


def _derive_status(cv_status: str, job_status: str | None) -> str:
    """Derive a single lifecycle status for frontend consumption.
    
    Combines cv_files.status and processing_jobs.status into one clear enum:
      failed | deleted | parsed | completed | processing | pending
    """
    if cv_status == "failed":
        return "failed"
    if cv_status == "deleted":
        return "deleted"
    if cv_status == "parsed":
        return "parsed"
    if cv_status == "completed":
        return "completed"
    if job_status in ("queued", "processing", "retrying"):
        return "processing"
    return "pending"


def _resume_score(analysis: CvAnalysis | None) -> float | None:
    return analysis.overall_score if analysis is not None else None


def _issue_count(analysis: CvAnalysis | None) -> int | None:
    if analysis is None:
        return None
    return sum(
        1
        for item in (*(analysis.ats_issues or []), *(analysis.formatting_issues or []))
        if not item.get("passed", True)
    )


# ──────────────────────────────────────────────────────────────────────
# POST /cvs — upload
# ──────────────────────────────────────────────────────────────────────


@router.post("/cvs", response_model=CvUploadAccepted, status_code=202)
async def upload_cv(
    request: Request,
    file: UploadFile,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Upload a CV file (PDF or DOCX). Validates, scans, stores, then enqueues extraction.

    Returns 202 immediately with cvId and processingJobId. The extraction pipeline
    (Docling → Textract → merge) runs asynchronously.

    Accepts either a real authenticated user or an anonymous trial session
    (Sprint 2) — this is one of the trial-accessible routes.
    Rate-limited per client IP (upload tier, see `10-security-plan.md` §9).
    """
    client_key = get_client_key(request)
    if not check_upload_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many upload attempts. Please wait and try again.",
        )

    # Read file content
    file_content = await file.read()

    # Validate type (magic bytes) and size
    try:
        mime_type = validate_file_type(file.filename or "unnamed", file_content)
        file_size = validate_file_size(file_content)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Malware scan moved off the response path (jbs-solution-sheet.md S6):
    # store to quarantine/ now, scan as the text_extract worker's first
    # step before extraction ever sees the bytes. A failed scan there
    # deletes the object and fails the CV with an explanatory
    # error_message — same user-facing outcome, off the upload's clock.
    storage_key = generate_storage_key(file.filename or "unnamed.pdf", prefix="quarantine/")

    # Store file
    await upload_file(file_content, storage_key, mime_type)

    # Create database records
    cv_file = CvFile(
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        filename=file.filename or "unnamed",
        mime_type=mime_type,
        file_size=file_size,
        storage_key=storage_key,
        status="pending",
    )
    session.add(cv_file)
    await session.flush()

    # Audit
    session.add(
        AuditEvent(
            user_id=identity.user_id,
            event_type="upload",
            entity_type="cv_file",
            entity_id=cv_file.id,
            actor_type="user" if identity.user else "trial_session",
            ip_address=request.client.host if request.client else None,
        )
    )

    # Kick off the extraction pipeline. create_processing_job commits
    # internally before dispatching to Celery — that same commit also
    # covers the cv_file and AuditEvent rows added above (same session,
    # same transaction), so nothing is left to commit here.
    processing_job = await start_extraction_pipeline(
        session=session,
        cv_file_id=cv_file.id,
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
    )

    logger.info(
        "cv_uploaded",
        cv_id=cv_file.id,
        job_id=processing_job.id,
        filename=file.filename,
    )

    return CvUploadAccepted(
        cv_id=cv_file.id,
        processing_job_id=processing_job.id,
        status="queued",
        filename=file.filename or "unnamed",
        file_size=file_size,
        mime_type=mime_type,
    )


# ──────────────────────────────────────────────────────────────────────
# GET /cvs — list
# ──────────────────────────────────────────────────────────────────────


@router.get("/cvs", response_model=CvListResponse)
async def list_cvs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """List uploaded CVs for the current user. Scoped by user_id (IDOR-safe)."""
    query = _active_cv_query(current_user.id)

    if status_filter:
        query = query.where(CvFile.status == status_filter)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.execute(count_query)).scalar() or 0

    # Get page
    query = query.order_by(CvFile.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(query)
    cv_files = result.scalars().all()

    # Batch-query most recent processing job per CV for status visibility
    job_status_map: dict[str, str] = {}
    # Batch-query most recent CvAnalysis per CV for resumeScore/issueCount —
    # one query for the whole page, same reasoning as job_status_map above:
    # an N+1 query per row here would scale with page size for no reason.
    analysis_map: dict[str, CvAnalysis] = {}
    if cv_files:
        cv_ids = [f.id for f in cv_files]
        job_result = await session.execute(
            select(ProcessingJob.source_entity_id, ProcessingJob.status)
            .where(
                ProcessingJob.source_entity_type == "cv_file",
                ProcessingJob.source_entity_id.in_(cv_ids),
            )
            .order_by(ProcessingJob.created_at.desc())
        )
        for source_id, status in job_result.all():
            if source_id not in job_status_map:
                job_status_map[source_id] = status

        analysis_result = await session.execute(
            select(CvAnalysis)
            .where(CvAnalysis.cv_file_id.in_(cv_ids))
            .order_by(CvAnalysis.created_at.desc())
        )
        for analysis in analysis_result.scalars().all():
            if analysis.cv_file_id not in analysis_map:
                analysis_map[analysis.cv_file_id] = analysis

    items = [
        CvFileResponse(
            id=f.id,
            original_filename=f.filename,
            mime_type=f.mime_type,
            file_size_bytes=f.file_size,
            status=_derive_status(f.status, job_status_map.get(f.id)),
            upload_status="stored" if f.storage_key else "pending",
            processing_status=f.status,
            job_status=job_status_map.get(f.id),
            resume_score=_resume_score(analysis_map.get(f.id)),
            issue_count=_issue_count(analysis_map.get(f.id)),
            created_at=f.created_at,
            updated_at=f.updated_at,
        )
        for f in cv_files
    ]

    return CvListResponse(items=items, total=total, limit=limit, offset=offset)


# ──────────────────────────────────────────────────────────────────────
# GET /cvs/{cvId} — metadata
# ──────────────────────────────────────────────────────────────────────


@router.get("/cvs/{cv_id}", response_model=CvFileResponse)
async def get_cv(
    cv_id: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Get CV metadata. Returns 404 if not found, not owned by current identity, or soft-deleted.

    Trial-accessible (Sprint 2) — a trial session needs to poll its own
    upload's processing status before an account exists to check it with.
    """
    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            identity_owner_filter(CvFile, identity),
            CvFile.deleted_at.is_(None),
        )
    )
    cv_file = result.scalar_one_or_none()

    if cv_file is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    # Look up most recent processing job status for this CV
    job_result = await session.execute(
        select(ProcessingJob.status)
        .where(
            ProcessingJob.source_entity_type == "cv_file",
            ProcessingJob.source_entity_id == cv_id,
        )
        .order_by(ProcessingJob.created_at.desc())
        .limit(1)
    )
    job_status = job_result.scalar()

    analysis_result = await session.execute(
        select(CvAnalysis)
        .where(CvAnalysis.cv_file_id == cv_id)
        .order_by(CvAnalysis.created_at.desc())
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()

    return CvFileResponse(
        id=cv_file.id,
        original_filename=cv_file.filename,
        mime_type=cv_file.mime_type,
        file_size_bytes=cv_file.file_size,
        status=_derive_status(cv_file.status, job_status),
        upload_status="stored" if cv_file.storage_key else "pending",
        processing_status=cv_file.status,
        job_status=job_status,
        resume_score=_resume_score(analysis),
        issue_count=_issue_count(analysis),
        created_at=cv_file.created_at,
        updated_at=cv_file.updated_at,
    )


# ──────────────────────────────────────────────────────────────────────
# DELETE /cvs/{cvId}
# ──────────────────────────────────────────────────────────────────────


@router.delete("/cvs/{cv_id}", status_code=202)
async def delete_cv(
    cv_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Delete a CV and derived records. Returns 404 if not owned by current user or already deleted."""
    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            CvFile.user_id == current_user.id,
            CvFile.deleted_at.is_(None),
        )
    )
    cv_file = result.scalar_one_or_none()

    if cv_file is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    cv_file.deleted_at = func.now()
    cv_file.status = "deleted"

    session.add(
        AuditEvent(
            user_id=current_user.id,
            event_type="deletion_requested",
            entity_type="cv_file",
            entity_id=cv_file.id,
            actor_type="user",
        )
    )

    await session.commit()
    logger.info("cv_deleted", cv_id=cv_id, user_id=current_user.id)


# ──────────────────────────────────────────────────────────────────────
# POST /cvs/{cvId}/reprocess
# ──────────────────────────────────────────────────────────────────────


@router.post("/cvs/{cv_id}/reprocess", status_code=202)
async def reprocess_cv(
    request: Request,
    cv_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Re-trigger the extraction pipeline. Creates new extraction passes.

    Rate-limited per client IP (upload tier — a reprocess re-runs the same
    extraction pipeline as a fresh upload).
    """
    client_key = get_client_key(request)
    if not check_upload_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many upload attempts. Please wait and try again.",
        )

    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            CvFile.user_id == current_user.id,
            CvFile.deleted_at.is_(None),
        )
    )
    cv_file = result.scalar_one_or_none()

    if cv_file is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    cv_file.status = "pending"
    processing_job = await start_extraction_pipeline(
        session=session, cv_file_id=cv_file.id, user_id=current_user.id
    )

    await session.commit()

    from app.schemas.jobs import ProcessingJobRef

    return ProcessingJobRef(job_id=processing_job.id, status="queued")


# ──────────────────────────────────────────────────────────────────────
# GET /cvs/{cvId}/raw-text
# ──────────────────────────────────────────────────────────────────────


@router.get("/cvs/{cv_id}/raw-text", response_model=CvRawTextResponse)
async def get_cv_raw_text(
    cv_id: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Get canonical merged extracted text.

    Trial-accessible: the tailor flow shows the extracted text back to the
    user before they run an analysis, and that has to work for an anonymous
    trial session — which is the only identity a first-time visitor has.
    Scoped by identity_owner_filter exactly like every other trial-
    accessible route, so this widens who can read their *own* text, not
    what anyone can read.
    """
    # Verify ownership via cv_file, excluding soft-deleted rows
    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            identity_owner_filter(CvFile, identity),
            CvFile.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    raw = await session.execute(
        select(CvRawText).where(CvRawText.cv_file_id == cv_id)
    )
    raw_text = raw.scalar_one_or_none()

    if raw_text is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Raw text not available yet.")

    return CvRawTextResponse(
        canonical_text=raw_text.canonical_text,
        ocr_used=raw_text.ocr_used,
        merge_strategy_metadata=raw_text.merge_strategy_metadata,
    )


# ──────────────────────────────────────────────────────────────────────
# GET /cvs/{cvId}/extraction-detail
# ──────────────────────────────────────────────────────────────────────


@router.get("/cvs/{cv_id}/extraction-detail", response_model=CvExtractionDetailResponse)
async def get_cv_extraction_detail(
    cv_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Get Docling and Textract pass outputs with completeness metadata."""
    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            CvFile.user_id == current_user.id,
            CvFile.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    passes_result = await session.execute(
        select(CvExtractionPass)
        .where(CvExtractionPass.cv_file_id == cv_id)
        .order_by(CvExtractionPass.created_at)
    )
    passes = passes_result.scalars().all()

    raw_result = await session.execute(
        select(CvRawText).where(CvRawText.cv_file_id == cv_id)
    )
    raw = raw_result.scalar_one_or_none()

    return CvExtractionDetailResponse(
        passes=[
            CvExtractionPassResponse(
                id=p.id,
                pass_type=p.pass_type,
                attempt_number=p.attempt_number,
                confidence_score=p.confidence_score,
                processing_duration_ms=p.processing_duration_ms,
                created_at=p.created_at,
            )
            for p in passes
        ],
        structural_validation=(
            StructuralValidationResult(**raw.structural_validation_result)
            if raw and raw.structural_validation_result
            else None
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# GET /cvs/{cvId}/parsed-profile  — Phase 2
# ──────────────────────────────────────────────────────────────────────


@router.get("/cvs/{cv_id}/parsed-profile")
async def get_cv_parsed_profile(
    cv_id: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Return the current structured candidate profile (Phase 2).

    Trial-accessible (Sprint 2) — a trial session needs its own
    profileVersionId to call POST /matches.
    """
    # Ownership check, excluding soft-deleted CVs
    cv_result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            identity_owner_filter(CvFile, identity),
            CvFile.deleted_at.is_(None),
        )
    )
    cv_file = cv_result.scalar_one_or_none()
    if cv_file is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found",
        )

    # Get current profile pointer
    profile_result = await session.execute(
        select(CvProfile).where(CvProfile.cv_file_id == cv_id)
    )
    profile = profile_result.scalar_one_or_none()

    if profile is None or profile.current_version_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No structured profile available yet. Wait for CV parsing to complete.",
        )

    version_result = await session.execute(
        select(CvProfileVersion).where(
            CvProfileVersion.id == profile.current_version_id
        )
    )
    version = version_result.scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile version not found.",
        )

    return {
        "cvId": cv_id,
        "profileVersionId": version.id,
        "versionNumber": version.version_number,
        "profileHash": version.profile_hash,
        "schemaVersion": version.schema_version,
        "validationStatus": version.validation_status,
        "confidenceSummary": version.confidence_summary,
        "structuredPayload": version.structured_payload,
        "createdAt": version.created_at.isoformat() if version.created_at else None,
    }

# ──────────────────────────────────────────────────────────────────────
# POST /cvs/{cv_id}/ats-check  — Product Extension #1
# ──────────────────────────────────────────────────────────────────────


@router.post("/cvs/{cv_id}/ats-check", status_code=202)
async def run_ats_check_for_cv(
    request: Request,
    cv_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Run an ATS structural-readability check against this CV.

    Authenticated only. Creates an async processing job (job_type
    'ats_check') and returns 202 with the job_id immediately.

    Rate-limited on the generation tier — same bucket as /matches, the
    other rules-based (non-LLM) analysis job in this codebase.
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait and try again.",
        )

    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            CvFile.user_id == current_user.id,
            CvFile.deleted_at.is_(None),
        )
    )
    cv_file = result.scalar_one_or_none()
    if cv_file is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    # create_processing_job commits internally (before dispatching to
    # Celery) — nothing left to commit here.
    processing_job = await create_processing_job(
        session=session,
        job_type="ats_check",
        source_entity_type="cv_file",
        source_entity_id=cv_file.id,
        user_id=current_user.id,
    )

    from app.schemas.jobs import ProcessingJobRef
    return ProcessingJobRef(job_id=processing_job.id, status="queued")


# ──────────────────────────────────────────────────────────────────────
# GET /cvs/{cv_id}/ats-check  — Product Extension #1
# ──────────────────────────────────────────────────────────────────────


@router.get("/cvs/{cv_id}/ats-check",
            response_model=AtsReadinessCheckResponse)
async def get_ats_check(
    cv_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Retrieve the latest ATS readiness result for this CV.

    Returns 404 if no check has been run yet.
    """
    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            CvFile.user_id == current_user.id,
            CvFile.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    check_result = await session.execute(
        select(AtsReadinessCheck)
        .where(AtsReadinessCheck.cv_file_id == cv_id)
        .order_by(AtsReadinessCheck.created_at.desc())
        .limit(1)
    )
    check = check_result.scalar_one_or_none()
    if check is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No ATS check result available yet.")

    return AtsReadinessCheckResponse(
        id=check.id,
        cv_id=check.cv_file_id,
        cv_profile_version_id=check.cv_profile_version_id,
        overall_score=float(check.overall_score),
        contact_info_parseable=check.contact_info_parseable,
        checks=check.checks,
        created_at=check.created_at,
    )


# ──────────────────────────────────────────────────────────────────────
# POST /cvs/{cv_id}/analysis  — LLM-based CV analysis
# ──────────────────────────────────────────────────────────────────────


@router.post("/cvs/{cv_id}/analysis", status_code=202)
async def run_cv_analysis(
    request: Request,
    cv_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Run (or re-run) the LLM-based CV analysis for this CV.

    Authenticated only, mirrors POST /cvs/{cv_id}/ats-check exactly.
    Normally this doesn't need to be called directly — process_text_extract
    auto-chains into it on every successful extraction — but it's exposed
    so a CV can be re-analyzed on demand (e.g. after a reprocess) without
    re-uploading.

    Rate-limited on the generation tier — this is a real paid LLM call,
    same bucket as /matches and /resume-rewrites.
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait and try again.",
        )

    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            CvFile.user_id == current_user.id,
            CvFile.deleted_at.is_(None),
        )
    )
    cv_file = result.scalar_one_or_none()
    if cv_file is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    # create_processing_job commits internally (before dispatching to
    # Celery) — nothing left to commit here.
    processing_job = await create_processing_job(
        session=session,
        job_type="cv_analyze",
        source_entity_type="cv_file",
        source_entity_id=cv_file.id,
        user_id=current_user.id,
    )

    from app.schemas.jobs import ProcessingJobRef
    return ProcessingJobRef(job_id=processing_job.id, status="queued")


# ──────────────────────────────────────────────────────────────────────
# GET /cvs/{cv_id}/analysis  — LLM-based CV analysis
# ──────────────────────────────────────────────────────────────────────


@router.get("/cvs/{cv_id}/analysis", response_model=CvAnalysisResponse)
async def get_cv_analysis(
    cv_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Retrieve the latest LLM-based CV analysis for this CV.

    Returns 404 if no analysis has run yet.
    """
    result = await session.execute(
        select(CvFile).where(
            CvFile.id == cv_id,
            CvFile.user_id == current_user.id,
            CvFile.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=cv_id, detail="CV not found.",
        )

    analysis_result = await session.execute(
        select(CvAnalysis)
        .where(CvAnalysis.cv_file_id == cv_id)
        .order_by(CvAnalysis.created_at.desc())
        .limit(1)
    )
    analysis = analysis_result.scalar_one_or_none()
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No analysis result available yet.")

    return CvAnalysisResponse(
        id=analysis.id,
        cv_id=analysis.cv_file_id,
        cv_profile_version_id=analysis.cv_profile_version_id,
        overall_score=analysis.overall_score,
        skillset_score=analysis.skillset_score,
        formatting_score=analysis.formatting_score,
        ats_issues=[CvIssueItem(**item) for item in (analysis.ats_issues or [])],
        formatting_issues=[CvIssueItem(**item) for item in (analysis.formatting_issues or [])],
        tips=analysis.tips or [],
        created_at=analysis.created_at,
    )
