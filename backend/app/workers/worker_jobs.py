"""Celery task implementations for the extraction pipeline.

Each task:
1. Loads the processing_job from the database
2. Runs the extraction/merge logic
3. Writes results to the database
4. Enqueues the next step in the pipeline
5. Updates job status (processing → completed / failed)

Per security plan §2: Docling worker runs with no outbound network.
Textract worker needs outbound to AWS Textract endpoint only.
"""

import hashlib
import json
import re
import time
import uuid
import structlog
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa  # used by _write_cv_profile_shim's version-count query

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from sqlalchemy import select, delete, create_engine, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.job_states import (
    PermanentWorkerError,
    ProcessingStatus,
    RetryableWorkerError,
    classify_error,
    transition_job_status,
)
from app.core.logging import get_logger
from app.core.metrics import (
    JOB_THROUGHPUT,
    JOB_DURATION_SECONDS,
    EXTRACTION_CHARS,
    MERGE_STRATEGY_COUNTER,
    STRUCTURAL_ANOMALY_COUNTER,
    LLM_TOKENS_COUNTER,
    LLM_GENERATION_COUNTER,
    COST_USD_COUNTER,
)
from app.core.metrics_push import push_worker_metrics
from app.core.storage import download_file
from app.db.models import (
    AtsReadinessCheck,
    AuditEvent,
    CvAnalysis,
    CoverLetterAnswer,
    CoverLetterDraft,
    CoverLetterQuestion,
    CoverLetterWorkflow,
    CvCertificationItem,
    CvEducationItem,
    CvExperienceItem,
    CvFile,
    CvExtractionPass,
    CvProfile,
    CvProfileVersion,
    CvProjectItem,
    CvRawText,
    CvSkillItem,
    Export,
    JobPost,
    JobPostProfile,
    MatchEvidenceItem,
    MatchRun,
    ProcessingJob,
    TailoredCvDraft,
    TailoredCvSection,
    TrialSession,
    User,
)
from app.extraction.parser_interface import ExtractionResult
from app.core.circuit_breaker import TEXTRACT_CIRCUIT
from app.workers.tasks import (
    enqueue_text_extract,
    enqueue_job_post_fetch,
    enqueue_match,
    enqueue_job_post_parse,
    enqueue_ats_check,
    enqueue_cv_analyze,
    enqueue_cv_generate,
    enqueue_cover_letter_generate,
    enqueue_export,
    enqueue_export_pdf,
    enqueue_coverage_report,
)

logger = get_logger(__name__)

# Synchronous engine for Celery workers (Celery tasks are not async).
# connect_timeout (psycopg2's own kwarg name, not SQLAlchemy's "timeout")
# bounds new-connection setup; pool_timeout bounds waiting for a pooled one.
_sync_engine = create_engine(
    settings.database_url,
    pool_timeout=30,
    connect_args={"connect_timeout": 10},
)


def _get_sync_session() -> Session:
    return Session(_sync_engine)


# ──────────────────────────────────────────────────────────────────────
# Text extraction worker — replaces decommissioned steps 3-6
#
# Steps 3 (Docling), 4 (Textract), 5 (merge/structural validation) and
# 6 (cv_parse) were decommissioned; their code is preserved verbatim in
# decommissioned/. Extraction is now a single call to the
# `extraction` service, which hosts Example's routes/extract.ts unchanged
# (pdftotext -layout for PDF, unzip + xmlToText for DOCX).
#
# Consequences, deliberately not papered over here:
#   - cv_raw_text.merge_strategy is "example_extract_v1" and
#     structural_validation_result is null. There is only one parser now,
#     so there is nothing to cross-validate against.
#   - No cv_profile_versions row is produced. Everything downstream that
#     reads structured_payload or cv_skill_items (match_engine,
#     cover_letter, ats_check, coverage, export_rendering) has no input.
#     See decommissioned/README.md.
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(RetryableWorkerError,),
    name="app.workers.worker_jobs.process_text_extract",
    queue="text_extract",
)
def process_text_extract(self, job_id: str) -> None:
    """Extract CV text via the Example-derived extraction service.

    1. Load the job and CV file
    2. Download the bytes from storage
    3. POST them to the extraction service (raw body + x-resume-filename)
    4. Write cv_raw_text
    5. Mark the CV file completed
    """
    import httpx

    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        transition_job_status(job, ProcessingStatus.PROCESSING)
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        cv_file = session.get(CvFile, job.source_entity_id)
        if cv_file is None:
            raise ValueError(f"CV file {job.source_entity_id} not found")

        file_content = download_file_sync(cv_file.storage_key)

        # Malware scan (jbs-solution-sheet.md S6) — moved here from the
        # upload request path. Must run before extraction is handed these
        # bytes; nothing before this point has read file_content for
        # anything but the scan itself.
        from app.services.malware_scan import scan_file_sync

        try:
            scan_file_sync(file_content)
        except ValueError as e:
            # Malware detected — permanent, and the quarantined object
            # must not linger.
            delete_file_sync(cv_file.storage_key)
            raise PermanentWorkerError(str(e)) from e
        except RuntimeError as e:
            # Scanner unavailable — an infra hiccup, not a verdict on the
            # file. Leave it in quarantine and retry.
            raise RetryableWorkerError(str(e)) from e

        cv_file.status = "extracting"
        session.commit()

        # extract.ts dispatches on the *extension* of x-resume-filename,
        # not on Content-Type or magic bytes, so the original filename has
        # to be forwarded intact or a PDF is read as UTF-8 text.
        response = httpx.post(
            f"{settings.extraction_service_url}/api/resume/extract",
            content=file_content,
            headers={
                "Content-Type": cv_file.mime_type or "application/octet-stream",
                "x-resume-filename": cv_file.filename or "unnamed.pdf",
            },
            timeout=settings.extraction_service_timeout_seconds,
        )

        if response.status_code != 200:
            # extract.ts returns 400 with a user-facing message for an
            # unreadable/unsupported file — a permanent failure, not worth
            # three retries. Anything else is treated as retryable.
            detail = ""
            try:
                detail = response.json().get("error", "")
            except Exception:
                detail = response.text[:200]
            if response.status_code == 400:
                raise PermanentWorkerError(f"Extraction rejected the file: {detail}")
            raise RetryableWorkerError(
                f"Extraction service returned {response.status_code}: {detail}"
            )

        payload = response.json()
        canonical_text = (payload.get("resumeText") or "").strip()
        if not canonical_text:
            raise PermanentWorkerError("Extraction service returned no text.")

        raw_text = CvRawText(
            cv_file_id=cv_file.id,
            canonical_text=canonical_text,
            characters=len(canonical_text),
            # One parser, so there is no "highest confidence wins" choice to
            # record and nothing to structurally validate against.
            merge_strategy="example_extract_v1",
            merge_strategy_metadata={
                "engine": "example/routes/extract.ts",
                "returned_filename": payload.get("originalFileName"),
            },
            ocr_used=False,
            structural_validation_result=None,
        )
        session.add(raw_text)

        cv_file.status = "completed"
        transition_job_status(job, ProcessingStatus.COMPLETED)
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        duration_s = time.monotonic() - t_start
        logger.info(
            "text_extract_complete",
            job_id=job_id,
            cv_id=cv_file.id,
            characters=len(canonical_text),
            duration_ms=int(duration_s * 1000),
        )
        JOB_THROUGHPUT.labels(job_type="text_extract", status="completed").inc()
        JOB_DURATION_SECONDS.labels(job_type="text_extract").observe(duration_s)
        EXTRACTION_CHARS.labels(pass_type="example_extract").observe(len(canonical_text))

        # Auto-chain into the LLM-based CV analysis step. Steps 5 and 6
        # (merge/cv_parse) are decommissioned, so there is no structured
        # profile to build the old way — process_cv_analyze is what now
        # produces both the analysis result and (via its FK-satisfying
        # shim) the CvProfileVersion/CvProfile row that MatchRun and
        # CoverLetterWorkflow still require. Hand-rolled rather than via
        # orchestration.create_processing_job: that helper is async
        # (AsyncSession) and this worker only ever holds a sync Session,
        # so it can't be called directly from here. task_key still uses
        # orchestration's own compute_task_key for the same idempotency
        # convention every other job-creating call site uses.
        #
        # The text_extract success above is already committed by this
        # point, so this whole block is wrapped defensively: a reprocess
        # (POST /cvs/{cv_id}/reprocess) recomputes the same deterministic
        # task_key as its first run, and the partial unique index on
        # task_key is not scoped to active jobs (see migration 013) — a
        # second attempt can collide with the first run's now-completed
        # row. That must never retroactively fail the text_extract job
        # that already succeeded; it's logged and the CV simply doesn't
        # get a fresh analysis chained this time.
        try:
            from app.services.orchestration import compute_task_key

            owner_id = cv_file.user_id or cv_file.trial_session_id
            analyze_job = ProcessingJob(
                job_type="cv_analyze",
                source_entity_type="cv_file",
                source_entity_id=cv_file.id,
                user_id=cv_file.user_id,
                trial_session_id=cv_file.trial_session_id,
                status="pending",
                task_key=(
                    compute_task_key("cv_analyze", cv_file.id, owner_id)
                    if owner_id else None
                ),
            )
            session.add(analyze_job)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("cv_analyze_chain_create_failed", cv_id=cv_file.id, error=str(e))
            analyze_job = None

        if analyze_job is not None:
            try:
                enqueue_cv_analyze(analyze_job.id)
            except Exception as e:
                analyze_job.status = "failed"
                analyze_job.last_error = "Failed to publish task to message broker."
                analyze_job.failed_at = datetime.now(timezone.utc)
                session.commit()
                logger.error("cv_analyze_publish_failed", job_id=analyze_job.id, error=str(e))

    except Exception as e:
        duration_s = time.monotonic() - t_start
        logger.error("text_extract_failed", job_id=job_id, error=str(e))
        JOB_THROUGHPUT.labels(job_type="text_extract", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="text_extract").observe(duration_s)

        error_type = classify_error(e)
        is_final_attempt = (
            error_type is not RetryableWorkerError
            or self.request.retries >= self.max_retries
        )
        try:
            target_status = (
                ProcessingStatus.FAILED if is_final_attempt else ProcessingStatus.RETRYING
            )
            transition_job_status(job, target_status, error=str(e))
            if is_final_attempt:
                job.failed_at = datetime.now(timezone.utc)
                cv_file = session.get(CvFile, job.source_entity_id)
                if cv_file:
                    cv_file.status = "failed"
                    cv_file.error_message = str(e)
            session.commit()
        except Exception:
            session.rollback()
        raise error_type(str(e)) from e
    finally:
        session.close()
        structlog.contextvars.unbind_contextvars("job_id")



# ──────────────────────────────────────────────────────────────────────
# Phase 3: Match analysis worker
# ──────────────────────────────────────────────────────────────────────


def _run_and_persist_match(session: Session, match_run: MatchRun):
    """Runs match_analysis.run_match_llm() for an existing MatchRun row
    (already pointing at a real cv_profile_version_id/job_post_profile_id)
    and persists the result onto it plus its MatchEvidenceItem rows.

    LLM-based replacement for the old match_engine.run_match() call — see
    app/services/match_analysis.py's module docstring and
    app/workers/worker_jobs.py's top-of-file comment. match_engine.py
    itself is untouched, still directly unit-tested, and no longer called
    from here. cv_profile_version_id still resolves to a real cv_file_id
    (process_cv_analyze's FK-satisfying shim guarantees a CvProfileVersion
    row exists for every analyzed CV), so this reads the CV's raw text via
    that cv_file_id rather than cv_profile_version.structured_payload,
    which is now just the minimal basics/skills shim payload, not a
    source of match-worthy CV detail.

    Extracted from process_match (Sprint 5) so the coverage-report
    reuse-or-run helper below can share the exact same matching+
    persistence path — same "don't duplicate the mechanism that has to
    stay identical" reasoning as generation_core.py's extraction in
    Sprint 4. Coverage reporting must never behave like a second,
    subtly-different matching engine (09-test-plan.md: a coverage_report
    must never differ from what a standalone POST /matches call would
    produce for the same CV/job-post pair).
    """
    from app.services.match_analysis import run_match_llm
    from app.db.models import CvProfileVersion, JobPostProfile

    cv_version = session.get(CvProfileVersion, match_run.cv_profile_version_id)
    if cv_version is None:
        raise ValueError(f"CvProfileVersion {match_run.cv_profile_version_id} not found")

    cv_raw_text = session.execute(
        select(CvRawText).where(CvRawText.cv_file_id == cv_version.cv_file_id)
    ).scalar_one_or_none()
    if cv_raw_text is None or not (cv_raw_text.canonical_text or "").strip():
        raise ValueError(f"CV raw text not available for cv_file {cv_version.cv_file_id}")

    jp_profile = session.get(JobPostProfile, match_run.job_post_profile_id)
    if jp_profile is None:
        raise ValueError(f"JobPostProfile {match_run.job_post_profile_id} not found")

    job_post = session.get(JobPost, jp_profile.job_post_id)
    if job_post is None or not (job_post.raw_text or "").strip():
        raise ValueError(f"JobPost raw_text not available for job_post_profile {jp_profile.id}")

    jp_dict = {
        "job_title": jp_profile.job_title,
        "employer": jp_profile.employer,
        "required_skills": jp_profile.required_skills or [],
        "preferred_skills": jp_profile.preferred_skills or [],
        "qualifications": jp_profile.qualifications or [],
        "keywords": jp_profile.keywords or [],
    }

    result = run_match_llm(cv_raw_text.canonical_text, job_post.raw_text, jp_dict)

    for item in result.evidence_items:
        session.add(MatchEvidenceItem(
            match_run_id=match_run.id,
            requirement_text=item.requirement_text,
            requirement_type=item.requirement_type,
            support_level=item.support_level,
            confidence=item.confidence,
            source_references=item.source_references or None,
            suggestion=item.suggestion,
            warning=item.warning,
        ))

    match_run.score = result.score
    match_run.supported_count = result.supported_count
    match_run.partial_count = result.partial_count
    match_run.unsupported_count = result.unsupported_count
    match_run.contradictory_count = result.contradictory_count
    match_run.unclear_count = result.unclear_count
    match_run.total_requirements = result.total_requirements
    match_run.summary_analysis = result.summary_analysis
    match_run.match_json = {
        "ats_issues": result.ats_issues,
        "formatting_issues": result.formatting_issues,
        "tips": result.tips,
    }
    match_run.status = "completed"
    match_run.completed_at = datetime.now(timezone.utc)

    # Real spend (§10 CostSpikeSuspect): gpt-4o-mini token-based pricing,
    # same rates cv_generate uses — see COST_USD_COUNTER's own comment.
    if result.prompt_tokens or result.completion_tokens:
        COST_USD_COUNTER.labels(call_type="match").inc(
            result.prompt_tokens * 0.150 / 1_000_000
            + result.completion_tokens * 0.600 / 1_000_000
        )
        push_worker_metrics("worker_match")

    return result


def _get_or_run_match(
    session: Session, *, user_id: str, cv_profile_version_id: str, job_post_profile_id: str,
) -> MatchRun:
    """Reuses an existing completed MatchRun for this exact
    (cv_profile_version_id, job_post_profile_id) pair if one exists —
    no such lookup existed before Sprint 5; POST /matches always created
    a fresh row unconditionally (confirmed via reading matches.py::
    create_match). Runs and persists a fresh one via
    _run_and_persist_match otherwise, same code path process_match
    itself uses."""
    existing = session.execute(
        select(MatchRun).where(
            MatchRun.cv_profile_version_id == cv_profile_version_id,
            MatchRun.job_post_profile_id == job_post_profile_id,
            MatchRun.status == "completed",
        ).order_by(MatchRun.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    match_run = MatchRun(
        user_id=user_id,
        cv_profile_version_id=cv_profile_version_id,
        job_post_profile_id=job_post_profile_id,
        status="pending",
    )
    session.add(match_run)
    session.flush()
    _run_and_persist_match(session, match_run)
    return match_run


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_match",
    queue="match",
)
def process_match(self, job_id: str) -> None:
    """Run evidence-based matching between a CV and a job post.

    Uses the LLM-based match engine (app/services/match_analysis.py) — the
    rules-based match_engine.py it replaced depended on a structured CV
    profile the decommissioned cv_parse step no longer produces.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        session.commit()

        match_run = session.get(MatchRun, job.source_entity_id)
        if match_run is None:
            raise ValueError(f"MatchRun {job.source_entity_id} not found")

        result = _run_and_persist_match(session, match_run)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        duration_s = time.monotonic() - t_start
        logger.info(
            "match_complete",
            match_id=match_run.id,
            score=result.score,
            supported=result.supported_count,
            unsupported=result.unsupported_count,
            duration_ms=int(duration_s * 1000),
        )

    except Exception as e:
        duration_s = time.monotonic() - t_start
        logger.error("match_failed", job_id=job_id, error=str(e))
        try:
            job.status = "failed"
            job.last_error = str(e)
            job.failed_at = datetime.now(timezone.utc)
            match_run = session.get(MatchRun, job.source_entity_id)
            if match_run:
                match_run.status = "failed"
                match_run.error_message = str(e)
            session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()
        structlog.contextvars.unbind_contextvars("job_id")


# ──────────────────────────────────────────────────────────────────────
# LLM-based CV analysis — replaces the decommissioned structured-parsing
# pipeline's role as the source of a per-CV quality signal, and (via the
# shim below) the source of the CvProfileVersion/CvProfile row that
# MatchRun and CoverLetterWorkflow still require. See
# app/services/cv_analysis.py and app/db/models.py::CvAnalysis.
# ──────────────────────────────────────────────────────────────────────


def _write_cv_profile_shim(
    session: Session, *, cv_file: CvFile, basics: dict, skills: list[str],
) -> CvProfileVersion:
    """Writes a minimal CvProfileVersion, upserts CvProfile.current_version_id
    to point at it, and creates CvSkillItem rows from `skills` — purely to
    satisfy the NOT NULL cv_profile_version_id FKs MatchRun and
    CoverLetterWorkflow still carry, now that the old exhaustive
    experience/education/certification/project parser (cv_parse) is
    decommissioned. Deliberately minimal: `structured_payload` here is
    just `{"basics": ..., "skills": ...}`, not a reimplementation of that
    parser's full schema — this is a product-approved shim, not a revival
    of the decommissioned pipeline.

    Called once per completed process_cv_analyze run. version_number
    increments per cv_file_id like the old cv_parse step did, so a
    reprocessed/re-analyzed CV gets a new version rather than mutating an
    existing (supposedly immutable, per CvProfileVersion's own docstring)
    row.
    """
    structured_payload = {"basics": basics, "skills": skills}
    profile_hash = hashlib.sha256(
        json.dumps(structured_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()

    existing_version_count = session.execute(
        select(sa.func.count()).select_from(CvProfileVersion).where(
            CvProfileVersion.cv_file_id == cv_file.id
        )
    ).scalar_one()
    version_number = (existing_version_count or 0) + 1

    version = CvProfileVersion(
        cv_file_id=cv_file.id,
        user_id=cv_file.user_id,
        trial_session_id=cv_file.trial_session_id,
        version_number=version_number,
        profile_hash=profile_hash,
        schema_version="llm_shim_v1",
        source_pass_ids=None,
        structured_payload=structured_payload,
        confidence_summary=None,
        validation_status="passed",
    )
    session.add(version)
    session.flush()

    profile = session.execute(
        select(CvProfile).where(CvProfile.cv_file_id == cv_file.id)
    ).scalar_one_or_none()
    if profile is None:
        session.add(CvProfile(cv_file_id=cv_file.id, current_version_id=version.id))
    else:
        profile.current_version_id = version.id

    for skill_name in skills:
        if not skill_name:
            continue
        session.add(CvSkillItem(
            cv_profile_version_id=version.id,
            skill_name=skill_name,
            category=None,
            confidence=None,
            source_reference=None,
        ))

    return version


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_cv_analyze",
    queue="cv_analyze",
)
def process_cv_analyze(self, job_id: str) -> None:
    """Run the LLM-based, job-agnostic CV analysis against a CV's raw
    text, persist a CvAnalysis row, and write the FK-satisfying
    CvProfileVersion/CvProfile/CvSkillItem shim (_write_cv_profile_shim
    above) so MatchRun/CoverLetterWorkflow's NOT NULL FKs resolve again.

    One-shot terminal job (like 'match'/'ats_check') — mirrors
    process_ats_check's structure. Auto-chained from process_text_extract
    on successful extraction (see the end of that task); can also be
    triggered directly via POST /cvs/{cv_id}/analysis.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        cv_file = session.get(CvFile, job.source_entity_id)
        if cv_file is None:
            raise ValueError(f"CV file {job.source_entity_id} not found")

        raw_text = session.execute(
            select(CvRawText).where(CvRawText.cv_file_id == cv_file.id)
        ).scalar_one_or_none()
        if raw_text is None or not (raw_text.canonical_text or "").strip():
            raise ValueError(f"CV raw text not available for cv_file {cv_file.id}")

        from app.services.cv_analysis import analyze_cv

        result = analyze_cv(raw_text.canonical_text)

        version = _write_cv_profile_shim(
            session, cv_file=cv_file, basics=result.basics, skills=result.skills,
        )

        session.add(CvAnalysis(
            cv_file_id=cv_file.id,
            cv_profile_version_id=version.id,
            overall_score=result.overall_score,
            skillset_score=result.skillset_score,
            formatting_score=result.formatting_score,
            ats_issues=result.ats_issues,
            formatting_issues=result.formatting_issues,
            tips=result.tips,
        ))

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        JOB_THROUGHPUT.labels(job_type="cv_analyze", status="completed").inc()
        duration_s = time.monotonic() - t_start
        JOB_DURATION_SECONDS.labels(job_type="cv_analyze").observe(duration_s)

        # Real spend (§10 CostSpikeSuspect): gpt-4o-mini token-based
        # pricing, same rates cv_generate uses — see COST_USD_COUNTER's
        # own comment.
        if result.prompt_tokens or result.completion_tokens:
            COST_USD_COUNTER.labels(call_type="cv_analyze").inc(
                result.prompt_tokens * 0.150 / 1_000_000
                + result.completion_tokens * 0.600 / 1_000_000
            )
            push_worker_metrics("worker_cv_analyze")

        logger.info(
            "cv_analyze_complete",
            job_id=job_id,
            cv_id=cv_file.id,
            overall_score=result.overall_score,
            skillset_score=result.skillset_score,
            formatting_score=result.formatting_score,
            duration_ms=int(duration_s * 1000),
        )

    except Exception as e:
        session.rollback()
        duration_s = time.monotonic() - t_start
        logger.error("cv_analyze_failed", job_id=job_id, error=str(e))
        JOB_THROUGHPUT.labels(job_type="cv_analyze", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="cv_analyze").observe(duration_s)
        try:
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = str(e)
                job.failed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as finalize_err:
            logger.error(
                "cv_analyze_finalize_failed",
                job_id=job_id, error=str(finalize_err),
            )
        raise
    finally:
        session.close()
        structlog.contextvars.unbind_contextvars("job_id")


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def download_file_sync(storage_key: str) -> bytes:
    """Synchronous wrapper for storage download (Celery tasks are sync)."""
    import boto3
    from botocore.config import Config as BotoConfig

    if settings.minio_endpoint and "minio" in settings.minio_endpoint:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint,
            aws_access_key_id=settings.minio_root_user,
            aws_secret_access_key=settings.minio_root_password,
            region_name=settings.aws_region,
            config=BotoConfig(signature_version="s3v4", connect_timeout=10, read_timeout=30),
        )
    else:
        s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=BotoConfig(connect_timeout=10, read_timeout=30),
        )

    response = s3.get_object(Bucket=settings.s3_bucket_name, Key=storage_key)
    return response["Body"].read()


def delete_file_sync(storage_key: str) -> None:
    """Synchronous wrapper for storage delete (Celery tasks are sync) —
    used to remove a quarantined upload that fails its malware scan
    (jbs-solution-sheet.md S6). A quarantined file that lingers after a
    failed scan is worse than one that was never stored."""
    import boto3
    from botocore.config import Config as BotoConfig

    if settings.minio_endpoint and "minio" in settings.minio_endpoint:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint,
            aws_access_key_id=settings.minio_root_user,
            aws_secret_access_key=settings.minio_root_password,
            region_name=settings.aws_region,
            config=BotoConfig(signature_version="s3v4", connect_timeout=10, read_timeout=30),
        )
    else:
        s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=BotoConfig(connect_timeout=10, read_timeout=30),
        )

    s3.delete_object(Bucket=settings.s3_bucket_name, Key=storage_key)


def upload_file_sync(file_content: bytes, storage_key: str, content_type: str) -> None:
    """Synchronous wrapper for storage upload (Celery tasks are sync) —
    symmetric to download_file_sync above. Needed by process_export_docx/
    process_export_pdf since Celery tasks can't call the async
    app.core.storage.upload_file directly."""
    import boto3
    from botocore.config import Config as BotoConfig

    if settings.minio_endpoint and "minio" in settings.minio_endpoint:
        s3 = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint,
            aws_access_key_id=settings.minio_root_user,
            aws_secret_access_key=settings.minio_root_password,
            region_name=settings.aws_region,
            config=BotoConfig(signature_version="s3v4", connect_timeout=10, read_timeout=30),
        )
    else:
        s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            config=BotoConfig(connect_timeout=10, read_timeout=30),
        )

    s3.put_object(Bucket=settings.s3_bucket_name, Key=storage_key, Body=file_content, ContentType=content_type)


def _get_next_attempt(session: Session, cv_file_id: str, pass_type: str) -> int:
    """Get the next attempt_number for a given pass_type on a cv_file."""
    from sqlalchemy import text

    max_attempt = session.execute(
        text(
            "SELECT COALESCE(MAX(attempt_number), 0) FROM cv_extraction_passes "
            "WHERE cv_file_id = :cv_id AND pass_type = :pt"
        ),
        {"cv_id": cv_file_id, "pt": pass_type},
    ).scalar()
    return (max_attempt or 0) + 1


def _average_textract_confidence(response: dict) -> float | None:
    """Compute average confidence from Textract LINE blocks."""
    confidences = []
    for block in response.get("Blocks", []):
        if block.get("BlockType") == "LINE" and "Confidence" in block:
            confidences.append(block["Confidence"])
    if not confidences:
        return None
    return round(sum(confidences) / len(confidences) / 100, 2)


# ──────────────────────────────────────────────────────────────────────
# Phase 2: Job post fetch worker (SSRF-safe)
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="app.workers.worker_jobs.process_job_post_fetch",
    queue="job_post_fetch",
)
def process_job_post_fetch(self, job_id: str) -> None:
    """Fetch a job post URL with SSRF-safe validation.

    Uses ssrf_safe_fetch() which validates scheme, DNS/IP, redirect chain,
    timeout, and response size per 10-security-plan.md §4.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        session.commit()

        jp = session.get(JobPost, job.source_entity_id)
        if jp is None:
            raise ValueError(f"JobPost {job.source_entity_id} not found")

        if not jp.source_url:
            raise ValueError("Job post has no source_url to fetch")

        from app.services.ssrf_safe_fetch import ssrf_safe_fetch, SSRFRejection, FetchError
        from app.services.job_post_extract import extract_job_text

        try:
            raw_text = ssrf_safe_fetch(jp.source_url)
        except SSRFRejection as e:
            logger.warning("ssrf_rejected", url=jp.source_url, reason=str(e))
            jp.status = "failed"
            jp.error_message = (
                f"URL rejected for security reasons. {e} "
                "Please paste the job description text directly instead."
            )
            job.status = "failed"
            job.last_error = str(e)
            job.failed_at = datetime.now(timezone.utc)
            session.commit()
            return
        except FetchError as e:
            logger.warning("fetch_failed", url=jp.source_url, reason=str(e))
            jp.status = "failed"
            jp.error_message = (
                f"Could not fetch the job posting. {e} "
                "Please paste the job description text directly instead."
            )
            job.status = "failed"
            job.last_error = str(e)
            job.failed_at = datetime.now(timezone.utc)
            session.commit()
            return

        # ssrf_safe_fetch returns the response body verbatim, so a normal
        # careers page arrives as markup wrapped in cookie banners, career
        # menus and footers. Reduce it to the posting here rather than in
        # the fetcher, which is generic: raw_text is shown to the user and
        # sent to the model, and neither wants the rest of the site.
        # Plain-text bodies are returned unchanged.
        raw_text = extract_job_text(raw_text)

        # Store fetched text and enqueue the parse step
        jp.raw_text = raw_text
        jp.status = "structuring"
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        logger.info(
            "job_post_fetched",
            job_post_id=jp.id,
            url=jp.source_url,
            char_count=len(raw_text),
        )

        # Update job_type to reflect current pipeline stage before handoff
        job.job_type = "job_post_parse"
        session.commit()

        # Enqueue the parse worker as the next step
        from app.workers.tasks import enqueue_job_post_parse

        enqueue_job_post_parse(job_id)

    except Exception as e:
        logger.error("job_post_fetch_failed", job_id=job_id, error=str(e))
        try:
            job.status = "failed"
            job.last_error = str(e)
            job.failed_at = datetime.now(timezone.utc)
            jp = session.get(JobPost, job.source_entity_id)
            if jp:
                jp.status = "failed"
                jp.error_message = str(e)
            session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()
        structlog.contextvars.unbind_contextvars("job_id")


# ──────────────────────────────────────────────────────────────────────
# Phase 2: Job post structuring worker
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_job_post_parse",
    queue="job_post_parse",
)
def process_job_post_parse(self, job_id: str) -> None:
    """Structure a fetched or pasted job post into a JobPostProfile.

    Uses RulesBasedJobPostParser (via the JobPostParser ABC) for a
    fast, no-LLM first pass. An LLM-backed parser can be swapped in
    later without changing this worker.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        session.commit()

        jp = session.get(JobPost, job.source_entity_id)
        if jp is None:
            raise ValueError(f"JobPost {job.source_entity_id} not found")

        if not jp.raw_text:
            raise ValueError("Job post has no raw_text to parse")

        # Parse using the rules-based parser via the ABC
        from app.extraction.job_post_parser import RulesBasedJobPostParser

        parser = RulesBasedJobPostParser()
        result = parser.parse(jp.raw_text)

        # M3 — LLM skill-extraction enrichment. Only runs when the
        # rules-based+taxonomy (M1/M2) parse found few requirements —
        # the specific prose-heavy-posting gap confirmed live that
        # neither a keyword list nor a taxonomy lookup can close on its
        # own. Purely additive: never overwrites what M1/M2 already
        # found, never blocks the parse on failure.
        from app.services.job_post_skill_extraction import (
            extract_skills_via_llm,
            should_enrich,
        )

        if should_enrich(result.required_skills, result.qualifications):
            enriched = extract_skills_via_llm(jp.raw_text)
            if enriched:
                existing_lower = {
                    s.lower() for s in (result.qualifications or [])
                }
                new_terms = [s for s in enriched if s.lower() not in existing_lower]
                if new_terms:
                    # New terms first: match_engine.py caps qualifications
                    # at 15 for scoring, and the short, discrete LLM-
                    # extracted phrases are the ones actually matchable
                    # against CV skill terms — the original long sentences
                    # (kept, for generation context) would otherwise
                    # crowd them out of the cap. Confirmed live: appending
                    # instead of prepending silently cut 7 of 14 useful
                    # terms before this fix.
                    result.qualifications = new_terms + (result.qualifications or [])
                    logger.info(
                        "job_post_llm_enrichment_applied",
                        job_post_id=jp.id,
                        new_terms=len(new_terms),
                    )

        # A parse that found nothing structured isn't a completed job
        # post — it's a failed one. A page an anti-bot challenge blocked,
        # a JS-rendered posting ssrf_safe_fetch never saw past the shell
        # of, or a paste that wasn't actually a job description all clear
        # extract_job_text and the parser without raising. Writing an
        # empty profile and marking this "completed" leaves the Jobs page
        # permanently showing a blank Role/Employer with no way to retry,
        # and — worse — lets a match run score a CV against nothing.
        # Raising here instead routes through the except block below,
        # which marks both rows "failed" and surfaces the existing
        # "paste the text instead" recovery flow.
        found_something = bool(
            result.job_title
            or result.employer
            or result.required_skills
            or result.qualifications
            or result.responsibilities
        )
        if not found_something:
            raise ValueError(
                "Couldn't find any job details on that page. "
                "Please paste the job description text instead."
            )

        # Upsert the profile row
        existing = session.execute(
            select(JobPostProfile).where(
                JobPostProfile.job_post_id == jp.id
            )
        ).scalar_one_or_none()

        if existing:
            existing.job_title = result.job_title
            existing.employer = result.employer
            existing.location = result.location
            existing.required_skills = result.required_skills
            existing.preferred_skills = result.preferred_skills
            existing.responsibilities = result.responsibilities
            existing.qualifications = result.qualifications
            existing.keywords = result.keywords
            existing.seniority = result.seniority
            existing.structured_json = result.model_dump()
            existing.confidence = result.confidence
        else:
            profile = JobPostProfile(
                job_post_id=jp.id,
                job_title=result.job_title,
                employer=result.employer,
                location=result.location,
                required_skills=result.required_skills,
                preferred_skills=result.preferred_skills,
                responsibilities=result.responsibilities,
                qualifications=result.qualifications,
                keywords=result.keywords,
                seniority=result.seniority,
                structured_json=result.model_dump(),
                confidence=result.confidence,
            )
            session.add(profile)

        jp.status = "completed"
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        duration_s = time.monotonic() - t_start
        logger.info(
            "job_post_parsed",
            job_post_id=jp.id,
            title=result.job_title,
            skill_count=len(result.required_skills or []),
            confidence=result.confidence,
            duration_ms=int(duration_s * 1000),
        )

    except Exception as e:
        duration_s = time.monotonic() - t_start
        logger.error("job_post_parse_failed", job_id=job_id, error=str(e))
        try:
            job.status = "failed"
            job.last_error = str(e)
            job.failed_at = datetime.now(timezone.utc)
            jp = session.get(JobPost, job.source_entity_id)
            if jp:
                jp.status = "failed"
                jp.error_message = str(e)
            session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()
        structlog.contextvars.unbind_contextvars("job_id")


# ──────────────────────────────────────────────────────────────────────
# Stalled-job recovery (outbox/recovery)
# ──────────────────────────────────────────────────────────────────────

# Every job_type a worker consumes, mapped to the enqueue helper that
# (re)publishes it. Recovery republishes via these, so a job orphaned
# between the API producer and the Celery/Redis broker — or never consumed
# by a worker — self-heals instead of sitting at pending/queued forever.
_JOB_TYPE_TO_ENQUEUE = {
    "text_extract": enqueue_text_extract,
    "job_post_fetch": enqueue_job_post_fetch,
    "match": enqueue_match,
    "job_post_parse": enqueue_job_post_parse,
    "ats_check": enqueue_ats_check,
    "cv_analyze": enqueue_cv_analyze,
    "cv_generate": enqueue_cv_generate,
    "cover_letter_generate": enqueue_cover_letter_generate,
    "export": enqueue_export,
    "export_pdf": enqueue_export_pdf,
    "coverage_report": enqueue_coverage_report,
}


@shared_task(
    name="app.workers.worker_jobs.recover_stalled_jobs",
    queue="maintenance",
)
def recover_stalled_jobs() -> dict:
    """Republish processing jobs that never reached a worker.

    A job stuck at pending/queued with no started_at is either (a) never
    published, (b) published but lost between the API producer and the
    broker, or (c) published but never consumed. All three look identical
    from outside. This task republishes such jobs via the same enqueue
    helpers the API uses, bounded by publish_attempts so it can't loop
    forever against a permanently-down worker.

    Idempotent by construction: it only touches a job whose most recent
    (re)publish is older than the min-age window, so two runs in quick
    succession do not double-publish the same job.
    """
    if not settings.stalled_job_recovery_enabled:
        return {"republished": 0}

    session = _get_sync_session()
    republished = 0
    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=settings.stalled_job_min_age_seconds)
        max_attempts = max(1, settings.stalled_job_max_publish_attempts)

        jobs = session.execute(
            select(ProcessingJob).where(
                ProcessingJob.status.in_(("pending", "queued")),
                ProcessingJob.started_at.is_(None),
                ProcessingJob.created_at < cutoff,
                ProcessingJob.publish_attempts < max_attempts,
                or_(
                    ProcessingJob.published_at.is_(None),
                    ProcessingJob.published_at < cutoff,
                ),
            )
        ).scalars().all()

        for job in jobs:
            enqueue_fn = _JOB_TYPE_TO_ENQUEUE.get(job.job_type)
            if enqueue_fn is None:
                logger.warning(
                    "recover_stalled_job_unknown_type",
                    job_id=job.id, job_type=job.job_type,
                )
                continue
            try:
                celery_task_id = enqueue_fn(str(job.id))
                job.published_at = now
                job.celery_task_id = celery_task_id
                job.last_publish_error = None
            except Exception as e:
                job.last_publish_error = str(e)
                logger.error(
                    "recover_stalled_job_publish_failed",
                    job_id=job.id, job_type=job.job_type, error=str(e),
                )
            job.publish_attempts = (job.publish_attempts or 0) + 1
            republished += 1
            logger.info(
                "recover_stalled_job_republished",
                job_id=job.id, job_type=job.job_type,
                attempt=job.publish_attempts,
            )

        session.commit()
        if republished:
            logger.info("recover_stalled_jobs_done", republished=republished)
    finally:
        session.close()
    return {"republished": republished}


# ──────────────────────────────────────────────────────────────────────
# Sprint 2: Anonymous trial support — expiry cleanup
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    name="app.workers.worker_jobs.cleanup_expired_trial_sessions",
    queue="maintenance",
)
def cleanup_expired_trial_sessions() -> None:
    """Delete expired, unclaimed trial sessions and everything still
    attached to them, per 06-non-functional-requirements.md's retention
    discipline — unclaimed trial data shouldn't accumulate indefinitely.

    Only UNCLAIMED sessions are touched: claim-trial already reassigns a
    claimed session's rows to a real user_id and clears trial_session_id,
    so a claimed session's data is never visible to the queries below
    regardless of how old the trial_session row itself is.

    Deletes in FK-dependency order (children before parents) since these
    relationships aren't set up for ON DELETE CASCADE at the DB level:
    processing_jobs and match_evidence_items first, then match_runs; the
    three cv_profile_versions child tables, then cv_profiles (which
    points at both cv_profile_versions and cv_files) and
    cv_profile_versions itself; cv_extraction_passes/cv_raw_text, then
    cv_files; job_post_profiles, then job_posts; finally the
    trial_sessions rows.

    Invoked periodically by Celery beat (`beat_schedule` in
    app/workers/tasks.py, interval set by
    settings.trial_session_cleanup_interval_seconds), consumed by the
    `worker_maintenance` service (docker-compose.yml) on the `maintenance`
    queue. Can also be triggered manually via `celery -A
    app.workers.tasks.celery_app call
    app.workers.worker_jobs.cleanup_expired_trial_sessions`.
    """
    session = _get_sync_session()
    try:
        now = datetime.now(timezone.utc)
        expired_ids = session.execute(
            select(TrialSession.id).where(
                TrialSession.expires_at <= now,
                TrialSession.claimed_by_user_id.is_(None),
            )
        ).scalars().all()

        if not expired_ids:
            logger.info("trial_session_cleanup_none_expired")
            return

        cv_file_ids = session.execute(
            select(CvFile.id).where(CvFile.trial_session_id.in_(expired_ids))
        ).scalars().all()
        cv_profile_version_ids = session.execute(
            select(CvProfileVersion.id).where(CvProfileVersion.trial_session_id.in_(expired_ids))
        ).scalars().all()
        job_post_ids = session.execute(
            select(JobPost.id).where(JobPost.trial_session_id.in_(expired_ids))
        ).scalars().all()
        match_run_ids = session.execute(
            select(MatchRun.id).where(MatchRun.trial_session_id.in_(expired_ids))
        ).scalars().all()

        session.execute(delete(ProcessingJob).where(ProcessingJob.trial_session_id.in_(expired_ids)))
        session.execute(delete(MatchEvidenceItem).where(MatchEvidenceItem.match_run_id.in_(match_run_ids)))
        session.execute(delete(MatchRun).where(MatchRun.id.in_(match_run_ids)))

        session.execute(delete(CvExperienceItem).where(CvExperienceItem.cv_profile_version_id.in_(cv_profile_version_ids)))
        session.execute(delete(CvEducationItem).where(CvEducationItem.cv_profile_version_id.in_(cv_profile_version_ids)))
        session.execute(delete(CvSkillItem).where(CvSkillItem.cv_profile_version_id.in_(cv_profile_version_ids)))
        session.execute(delete(CvCertificationItem).where(CvCertificationItem.cv_profile_version_id.in_(cv_profile_version_ids)))
        session.execute(delete(CvProjectItem).where(CvProjectItem.cv_profile_version_id.in_(cv_profile_version_ids)))
        session.execute(delete(CvProfile).where(CvProfile.cv_file_id.in_(cv_file_ids)))
        session.execute(delete(CvProfileVersion).where(CvProfileVersion.id.in_(cv_profile_version_ids)))

        session.execute(delete(CvExtractionPass).where(CvExtractionPass.cv_file_id.in_(cv_file_ids)))
        session.execute(delete(CvRawText).where(CvRawText.cv_file_id.in_(cv_file_ids)))
        session.execute(delete(CvFile).where(CvFile.id.in_(cv_file_ids)))

        session.execute(delete(JobPostProfile).where(JobPostProfile.job_post_id.in_(job_post_ids)))
        session.execute(delete(JobPost).where(JobPost.id.in_(job_post_ids)))

        session.execute(delete(TrialSession).where(TrialSession.id.in_(expired_ids)))

        session.commit()
        logger.info(
            "trial_session_cleanup_complete",
            expired_sessions=len(expired_ids),
            cv_files=len(cv_file_ids),
            job_posts=len(job_post_ids),
            match_runs=len(match_run_ids),
        )
    except Exception as e:
        session.rollback()
        logger.error("trial_session_cleanup_failed", error=str(e))
        raise
    finally:
        session.close()




# ──────────────────────────────────────────────────────────────────────
# Product Extension #1: ATS structural-readiness check
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_ats_check",
    queue="ats_check",
)
def process_ats_check(self, job_id: str) -> None:
    """Run the rules-based ATS readiness check against a CV's merged
    extraction and structured profile.

    This is a one-shot terminal job (like 'match' / 'cv_generate') — it
    never transitions to another job_type, only completes or fails.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        cv_file = session.get(CvFile, job.source_entity_id)
        if cv_file is None:
            raise ValueError(f"CV file {job.source_entity_id} not found")

        # Resolve the current profile version via the pointer row
        profile = session.execute(
            select(CvProfile).where(CvProfile.cv_file_id == cv_file.id)
        ).scalar_one_or_none()

        cv_profile_version_id = None
        structured_payload = None
        if profile is not None and profile.current_version_id is not None:
            cv_profile_version_id = profile.current_version_id
            pv = session.get(CvProfileVersion, profile.current_version_id)
            if pv is not None:
                structured_payload = pv.structured_payload

        # Gather extraction data
        raw_text = session.execute(
            select(CvRawText).where(CvRawText.cv_file_id == cv_file.id)
        ).scalar_one_or_none()

        canonical_text = raw_text.canonical_text if raw_text else ""
        ocr_used = raw_text.ocr_used if raw_text else False
        merge_meta = raw_text.merge_strategy_metadata if raw_text else None
        structural_validation = (
            raw_text.structural_validation_result if raw_text else None
        )

        # Docling and Textract pass texts (needed for text_in_image check)
        passes = session.execute(
            select(CvExtractionPass)
            .where(CvExtractionPass.cv_file_id == cv_file.id)
        ).scalars().all()

        docling_text = ""
        textract_text = ""
        for p in passes:
            if p.pass_type == "docling":
                docling_text = p.extracted_text or ""
            elif p.pass_type == "textract":
                textract_text = p.extracted_text or ""

        from app.extraction.ats_check import run_ats_check as ats_scorer

        result = ats_scorer(
            canonical_text=canonical_text,
            docling_text=docling_text,
            textract_text=textract_text,
            ocr_used=ocr_used,
            structural_validation=structural_validation,
            structured_payload=structured_payload,
            mime_type=cv_file.mime_type or "",
            merge_strategy_metadata=merge_meta,
        )

        check_row = AtsReadinessCheck(
            cv_file_id=cv_file.id,
            cv_profile_version_id=cv_profile_version_id,
            overall_score=result.overall_score,
            checks=result.checks,
            contact_info_parseable=result.contact_info_parseable,
        )
        session.add(check_row)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        JOB_THROUGHPUT.labels(job_type="ats_check", status="completed").inc()
        duration_s = time.monotonic() - t_start
        JOB_DURATION_SECONDS.labels(job_type="ats_check").observe(duration_s)
        logger.info(
            "ats_check_complete",
            job_id=job_id,
            cv_id=cv_file.id,
            overall_score=result.overall_score,
        )

    except Exception as e:
        session.rollback()
        duration_s = time.monotonic() - t_start
        logger.error("ats_check_failed", job_id=job_id, error=str(e))
        JOB_THROUGHPUT.labels(job_type="ats_check", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="ats_check").observe(duration_s)
        try:
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = str(e)
                job.failed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as finalize_err:
            logger.error(
                "ats_check_finalize_failed",
                job_id=job_id, error=str(finalize_err),
            )
        raise
    finally:
        session.close()


# ──────────────────────────────────────────────────────────────────────
# Sprint 3: Tailored CV generation
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_cv_generate",
    queue="cv_generate",
)
def process_cv_generate(self, job_id: str) -> None:
    """Generate a tailored CV draft's sections from its match_run's
    evidence. One-shot terminal job, like 'match'/'ats_check' — never
    transitions to another job_type.

    Deliberately low max_retries (1, not the usual 3): a schema/
    verification failure inside generate_draft_sections() already retries
    internally per section (settings.tailored_cv_max_generation_retries)
    and degrades gracefully by omitting sections, not by raising — this
    task only raises for something outside that (DB error, missing rows),
    which a Celery-level retry is unlikely to fix by itself.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        draft = session.get(TailoredCvDraft, job.source_entity_id)
        if draft is None:
            raise ValueError(f"TailoredCvDraft {job.source_entity_id} not found")

        match_run = session.get(MatchRun, draft.match_run_id)
        if match_run is None:
            raise ValueError(f"MatchRun {draft.match_run_id} not found")

        match_evidence_items = session.execute(
            select(MatchEvidenceItem).where(MatchEvidenceItem.match_run_id == match_run.id)
        ).scalars().all()

        experience_items = session.execute(
            select(CvExperienceItem).where(
                CvExperienceItem.cv_profile_version_id == match_run.cv_profile_version_id
            )
        ).scalars().all()
        education_items = session.execute(
            select(CvEducationItem).where(
                CvEducationItem.cv_profile_version_id == match_run.cv_profile_version_id
            )
        ).scalars().all()
        skill_items = session.execute(
            select(CvSkillItem).where(
                CvSkillItem.cv_profile_version_id == match_run.cv_profile_version_id
            )
        ).scalars().all()
        certification_items = session.execute(
            select(CvCertificationItem).where(
                CvCertificationItem.cv_profile_version_id == match_run.cv_profile_version_id
            )
        ).scalars().all()
        project_items = session.execute(
            select(CvProjectItem).where(
                CvProjectItem.cv_profile_version_id == match_run.cv_profile_version_id
            )
        ).scalars().all()

        jp_profile = session.get(JobPostProfile, match_run.job_post_profile_id)
        job_requirements = [
            *(jp_profile.required_skills or [] if jp_profile else []),
            *(jp_profile.preferred_skills or [] if jp_profile else []),
            *(jp_profile.qualifications or [] if jp_profile else []),
            *(jp_profile.responsibilities or [] if jp_profile else []),
        ]

        # Full CV + job post text for the single-call body fallback in
        # generate_draft_sections (doc 17 §7): the row-driven generators read
        # profile rows production cv_analyze doesn't create. Same sources the
        # match engine itself uses (CvRawText.canonical_text /
        # job_posts.raw_text). Missing text ⇒ fallback silently off.
        cv_text = None
        cv_text_source_id = None
        cv_version = session.get(CvProfileVersion, match_run.cv_profile_version_id)
        if cv_version is not None:
            cv_raw = session.execute(
                select(CvRawText).where(CvRawText.cv_file_id == cv_version.cv_file_id)
            ).scalar_one_or_none()
            if cv_raw is not None and (cv_raw.canonical_text or "").strip():
                cv_text = cv_raw.canonical_text
                cv_text_source_id = cv_raw.id

        job_post_text = None
        target_title = None
        if jp_profile is not None:
            job_post = session.get(JobPost, jp_profile.job_post_id)
            if job_post is not None and (job_post.raw_text or "").strip():
                job_post_text = job_post.raw_text
            target_title = jp_profile.job_title or None

        from app.services.tailored_cv_generation import (
            generate_draft_sections, assemble_content_json, render_text_from_sections,
            build_validation_result, build_improvement_checklist,
        )

        outcome = generate_draft_sections(
            match_evidence_items=match_evidence_items,
            experience_items=experience_items,
            education_items=education_items,
            skill_items=skill_items,
            certification_items=certification_items,
            project_items=project_items,
            job_requirements=job_requirements,
            instructions=draft.instructions,
            cv_text=cv_text,
            job_post_text=job_post_text,
            target_title=target_title,
            cv_text_source_id=cv_text_source_id,
        )

        for section in outcome.sections:
            session.add(TailoredCvSection(
                draft_id=draft.id,
                section_type=section.section_type,
                content_text=section.content_text,
                evidence_references=section.evidence_references,
                generation_task=section.generation_task,
                prompt_version=section.prompt_version,
                model_id=section.model_id,
                validation_status=section.validation_status,
                order_index=section.order_index,
                source_item_id=section.source_item_id,
            ))

        draft.content_json = assemble_content_json(outcome.sections)
        draft.render_text = render_text_from_sections(outcome.sections)
        draft.validation_result = build_validation_result(outcome)
        draft.improvement_checklist = build_improvement_checklist(match_evidence_items)
        draft.status = "generated" if outcome.sections else "failed"
        draft.updated_at = datetime.now(timezone.utc)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        LLM_TOKENS_COUNTER.labels(generation_task="tailored_cv_all", token_type="prompt").inc(
            outcome.total_prompt_tokens
        )
        LLM_TOKENS_COUNTER.labels(generation_task="tailored_cv_all", token_type="completion").inc(
            outcome.total_completion_tokens
        )
        LLM_GENERATION_COUNTER.labels(
            generation_task="tailored_cv_draft",
            outcome="success" if outcome.sections else "verification_failed",
        ).inc()

        # Real spend (§10 CostSpikeSuspect): gpt-4o-mini is $0.150/1M prompt
        # tokens, $0.600/1M completion tokens — actual usage, not a flat guess.
        COST_USD_COUNTER.labels(call_type="cv_generate").inc(
            outcome.total_prompt_tokens * 0.150 / 1_000_000
            + outcome.total_completion_tokens * 0.600 / 1_000_000
        )
        push_worker_metrics("worker_cv_generate")

        JOB_THROUGHPUT.labels(job_type="cv_generate", status="completed").inc()
        duration_s = time.monotonic() - t_start
        JOB_DURATION_SECONDS.labels(job_type="cv_generate").observe(duration_s)
        logger.info(
            "cv_generate_complete",
            job_id=job_id,
            draft_id=draft.id,
            sections_generated=len(outcome.sections),
            issues=outcome.issues,
        )

    except Exception as e:
        session.rollback()
        duration_s = time.monotonic() - t_start
        logger.error("cv_generate_failed", job_id=job_id, error=str(e))
        LLM_GENERATION_COUNTER.labels(generation_task="tailored_cv_draft", outcome="api_error").inc()
        JOB_THROUGHPUT.labels(job_type="cv_generate", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="cv_generate").observe(duration_s)
        try:
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = str(e)
                job.failed_at = datetime.now(timezone.utc)
            draft = session.get(TailoredCvDraft, job.source_entity_id) if job is not None else None
            if draft is not None:
                draft.status = "failed"
            session.commit()
        except Exception as finalize_err:
            logger.error(
                "cv_generate_finalize_failed",
                job_id=job_id, error=str(finalize_err),
            )
        raise
    finally:
        session.close()


# ──────────────────────────────────────────────────────────────────────
# Sprint 4: Cover letter generation
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_cover_letter_generate",
    queue="cover_letter_generate",
)
def process_cover_letter_generate(self, job_id: str) -> None:
    """Generate a cover letter draft's body from its workflow's CV/job-post/
    answers. One-shot terminal job, mirrors process_cv_generate exactly.

    Deliberately low max_retries (1): internal retry-then-fallback already
    happens inside cover_letter_generation.generate_draft() and degrades
    gracefully (real LLM failure -> deterministic template, never raises
    for a generation-quality reason) — a Celery-level retry is only useful
    for genuine infra failures (DB down), not generation-quality ones.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        draft = session.get(CoverLetterDraft, job.source_entity_id)
        if draft is None:
            raise ValueError(f"CoverLetterDraft {job.source_entity_id} not found")

        wf = session.get(CoverLetterWorkflow, draft.workflow_id)
        if wf is None:
            raise ValueError(f"CoverLetterWorkflow {draft.workflow_id} not found")

        cv_version = session.get(CvProfileVersion, wf.cv_profile_version_id)
        basics = (cv_version.structured_payload or {}).get("basics", {}) if cv_version else {}
        cv_name = (basics or {}).get("name")

        jp_profile = session.get(JobPostProfile, wf.job_post_profile_id)
        job_title = (jp_profile.job_title if jp_profile else None) or "this role"
        employer_name = jp_profile.employer if jp_profile else None
        job_requirements = [
            *(jp_profile.required_skills or [] if jp_profile else []),
            *(jp_profile.preferred_skills or [] if jp_profile else []),
            *(jp_profile.qualifications or [] if jp_profile else []),
            *(jp_profile.responsibilities or [] if jp_profile else []),
        ]

        match_evidence_items = []
        if wf.match_run_id:
            match_evidence_items = session.execute(
                select(MatchEvidenceItem).where(MatchEvidenceItem.match_run_id == wf.match_run_id)
            ).scalars().all()

        experience_items = session.execute(
            select(CvExperienceItem).where(
                CvExperienceItem.cv_profile_version_id == wf.cv_profile_version_id
            )
        ).scalars().all()
        education_items = session.execute(
            select(CvEducationItem).where(
                CvEducationItem.cv_profile_version_id == wf.cv_profile_version_id
            )
        ).scalars().all()
        skill_items = session.execute(
            select(CvSkillItem).where(
                CvSkillItem.cv_profile_version_id == wf.cv_profile_version_id
            )
        ).scalars().all()
        certification_items = session.execute(
            select(CvCertificationItem).where(
                CvCertificationItem.cv_profile_version_id == wf.cv_profile_version_id
            )
        ).scalars().all()
        project_items = session.execute(
            select(CvProjectItem).where(
                CvProjectItem.cv_profile_version_id == wf.cv_profile_version_id
            )
        ).scalars().all()

        all_questions = session.execute(
            select(CoverLetterQuestion).where(CoverLetterQuestion.workflow_id == wf.id)
        ).scalars().all()
        questions_by_id = {q.id: q for q in all_questions}
        question_step_map = {q.id: q.step_number for q in all_questions}

        all_answers = session.execute(
            select(CoverLetterAnswer).where(
                CoverLetterAnswer.workflow_id == wf.id,
            ).order_by(CoverLetterAnswer.submitted_at)
        ).scalars().all()

        answers_by_step: dict[int, list[tuple[str, str]]] = {}
        for ans in all_answers:
            step = question_step_map.get(ans.question_id, 1)
            answers_by_step.setdefault(step, []).append((ans.id, ans.answer_text))

        tone = next(
            (text for _, text in answers_by_step.get(3, [])
             if text and any(word in text.lower() for word in ("formal", "enthusiastic", "concise", "detailed"))),
            None,
        )

        from app.services.cover_letter_generation import build_evidence_pool, generate_draft

        evidence_pool = build_evidence_pool(
            match_evidence_items=match_evidence_items,
            experience_items=experience_items,
            education_items=education_items,
            skill_items=skill_items,
            certification_items=certification_items,
            project_items=project_items,
            questions_by_id=questions_by_id,
            answers=all_answers,
        )

        result = generate_draft(
            evidence_pool=evidence_pool,
            job_requirements=job_requirements,
            job_title=job_title,
            employer_name=employer_name,
            cv_name=cv_name,
            tone=tone,
            answers_by_step=answers_by_step,
            experience_items=experience_items,
            project_items=project_items,
            skill_items=skill_items,
        )

        draft.body_text = result.body_text
        draft.evidence_references = result.evidence_references or None
        draft.tone = tone
        draft.prompt_version = result.prompt_version
        draft.model_id = result.model_id
        draft.status = "generated" if result.body_text else "failed"
        draft.updated_at = datetime.now(timezone.utc)

        wf.status = "draft_ready" if result.body_text else "generation_failed"
        wf.completed_at = datetime.now(timezone.utc)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        LLM_GENERATION_COUNTER.labels(
            generation_task="cover_letter_body",
            outcome="success" if result.source == "llm" else "fallback",
        ).inc()

        if result.source == "llm":
            # Real spend (§10 CostSpikeSuspect): cover_letter_generation.py
            # doesn't propagate token counts up (unlike tailored-CV
            # generation), so this uses the documented flat per-call estimate
            # (02-architecture-overview.md §10, ~$0.025/call for gpt-4o-mini
            # on a letter-length completion) rather than a real token count.
            # Only charged on the real LLM path — the template fallback made
            # no paid call.
            COST_USD_COUNTER.labels(call_type="cover_letter_generate").inc(0.025)
            push_worker_metrics("worker_cover_letter_generate")

        JOB_THROUGHPUT.labels(job_type="cover_letter_generate", status="completed").inc()
        duration_s = time.monotonic() - t_start
        JOB_DURATION_SECONDS.labels(job_type="cover_letter_generate").observe(duration_s)
        logger.info(
            "cover_letter_generate_complete",
            job_id=job_id, draft_id=draft.id, source=result.source,
        )

    except Exception as e:
        session.rollback()
        duration_s = time.monotonic() - t_start
        logger.error("cover_letter_generate_failed", job_id=job_id, error=str(e))
        JOB_THROUGHPUT.labels(job_type="cover_letter_generate", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="cover_letter_generate").observe(duration_s)
        try:
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = str(e)
                job.failed_at = datetime.now(timezone.utc)
            draft = session.get(CoverLetterDraft, job.source_entity_id) if job is not None else None
            if draft is not None:
                draft.status = "failed"
                wf = session.get(CoverLetterWorkflow, draft.workflow_id)
                if wf is not None:
                    wf.status = "generation_failed"
            session.commit()
        except Exception as finalize_err:
            logger.error(
                "cover_letter_generate_finalize_failed",
                job_id=job_id, error=str(finalize_err),
            )
        raise
    finally:
        session.close()


# ──────────────────────────────────────────────────────────────────────
# Sprint 5: Exports
# ──────────────────────────────────────────────────────────────────────

_EXPORT_CONTENT_TYPE_BY_EXT = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "zip": "application/zip",
    "pdf": "application/pdf",
}


def _resolve_owner_email(session: Session, export: Export) -> str | None:
    if export.user_id:
        user = session.get(User, export.user_id)
        return user.email if user else None
    return None


def _render_tailored_cv_docx(
    session: Session, draft: TailoredCvDraft, template_id: str | None, owner_email: str | None,
) -> bytes:
    from app.services import export_rendering, export_templates

    match_run = session.get(MatchRun, draft.match_run_id)
    cv_version = session.get(CvProfileVersion, match_run.cv_profile_version_id) if match_run else None
    basics = (cv_version.structured_payload or {}).get("basics", {}) if cv_version else {}

    sections = session.execute(
        select(TailoredCvSection).where(TailoredCvSection.draft_id == draft.id)
    ).scalars().all()

    experience_items = []
    project_items = []
    if match_run is not None:
        experience_items = session.execute(
            select(CvExperienceItem).where(
                CvExperienceItem.cv_profile_version_id == match_run.cv_profile_version_id
            )
        ).scalars().all()
        project_items = session.execute(
            select(CvProjectItem).where(
                CvProjectItem.cv_profile_version_id == match_run.cv_profile_version_id
            )
        ).scalars().all()

    context = export_rendering.build_cv_docx_context(
        candidate_name=export_rendering.resolve_candidate_name(basics),
        contact_line=export_rendering.resolve_contact_line(basics, fallback_email=owner_email),
        sections=sections,
        experience_by_id={e.id: e for e in experience_items},
        project_by_id={p.id: p for p in project_items},
    )
    resolved_template_id = export_templates.resolve_cv_template_id(template_id)
    return export_rendering.render_docx(export_templates.cv_template_path(resolved_template_id), context)


def _render_cover_letter_docx(session: Session, cl_draft: CoverLetterDraft, owner_email: str | None) -> bytes:
    from app.services import export_rendering, export_templates

    wf = session.get(CoverLetterWorkflow, cl_draft.workflow_id)
    cv_version = session.get(CvProfileVersion, wf.cv_profile_version_id) if wf else None
    basics = (cv_version.structured_payload or {}).get("basics", {}) if cv_version else {}
    jp_profile = session.get(JobPostProfile, wf.job_post_profile_id) if wf else None

    context = export_rendering.build_cover_letter_docx_context(
        candidate_name=export_rendering.resolve_candidate_name(basics),
        contact_line=export_rendering.resolve_contact_line(basics, fallback_email=owner_email),
        sent_date=datetime.now(timezone.utc).strftime("%B %d, %Y"),
        employer_name=jp_profile.employer if jp_profile else None,
        body_text=cl_draft.body_text,
    )
    return export_rendering.render_docx(export_templates.COVER_LETTER_TEMPLATE_FILE, context)


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_export_docx",
    queue="export",
)
def process_export_docx(self, job_id: str) -> None:
    """Render an approved draft into a downloadable docx (or a zip of two
    docx files, for an application pack) — one-shot terminal job, mirrors
    process_cv_generate. Deliberately low max_retries (1): a render/
    upload failure here is a real template or infra bug, not something a
    blind retry is likely to fix."""
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        export = session.get(Export, job.source_entity_id)
        if export is None:
            raise ValueError(f"Export {job.source_entity_id} not found")

        owner_email = _resolve_owner_email(session, export)

        if export.export_type == "cv":
            draft = session.get(TailoredCvDraft, export.source_id)
            if draft is None:
                raise ValueError(f"TailoredCvDraft {export.source_id} not found")
            file_bytes = _render_tailored_cv_docx(session, draft, export.template_id, owner_email)
            ext = "docx"
        elif export.export_type == "cover_letter":
            cl_draft = session.get(CoverLetterDraft, export.source_id)
            if cl_draft is None:
                raise ValueError(f"CoverLetterDraft {export.source_id} not found")
            file_bytes = _render_cover_letter_docx(session, cl_draft, owner_email)
            ext = "docx"
        elif export.export_type == "application_pack":
            from app.services import export_rendering

            cv_draft = session.get(TailoredCvDraft, export.source_id)
            cl_draft = session.get(CoverLetterDraft, export.secondary_source_id)
            if cv_draft is None or cl_draft is None:
                raise ValueError(f"Source draft(s) not found for application-pack export {export.id}")
            cv_bytes = _render_tailored_cv_docx(session, cv_draft, export.template_id, owner_email)
            cl_bytes = _render_cover_letter_docx(session, cl_draft, owner_email)
            file_bytes = export_rendering.build_application_pack_zip(
                cv_docx=cv_bytes, cover_letter_docx=cl_bytes,
            )
            ext = "zip"
        else:
            raise ValueError(f"Unknown export_type: {export.export_type}")

        storage_key = f"exports/{uuid.uuid4().hex}.{ext}"
        upload_file_sync(file_bytes, storage_key, _EXPORT_CONTENT_TYPE_BY_EXT[ext])

        export.storage_key = storage_key
        export.file_size = len(file_bytes)
        export.status = "completed"
        export.completed_at = datetime.now(timezone.utc)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)

        session.add(AuditEvent(
            user_id=export.user_id,
            entity_type="export",
            entity_id=export.id,
            event_type="export_generated",
            actor_type="system_worker",
            metadata_={"format": export.format, "template_id": export.template_id},
        ))
        session.commit()

        JOB_THROUGHPUT.labels(job_type="export", status="completed").inc()
        duration_s = time.monotonic() - t_start
        JOB_DURATION_SECONDS.labels(job_type="export").observe(duration_s)
        logger.info(
            "export_docx_complete",
            job_id=job_id, export_id=export.id, export_type=export.export_type, file_size=export.file_size,
        )

    except Exception as e:
        session.rollback()
        duration_s = time.monotonic() - t_start
        logger.error("export_docx_failed", job_id=job_id, error=str(e))
        JOB_THROUGHPUT.labels(job_type="export", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="export").observe(duration_s)
        try:
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = str(e)
                job.failed_at = datetime.now(timezone.utc)
            export = session.get(Export, job.source_entity_id) if job is not None else None
            if export is not None:
                export.status = "failed"
                export.error_message = str(e)
            session.commit()
        except Exception as finalize_err:
            logger.error("export_docx_finalize_failed", job_id=job_id, error=str(finalize_err))
        raise
    finally:
        session.close()


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    name="app.workers.worker_jobs.process_export_pdf",
    queue="export_pdf",
)
def process_export_pdf(self, job_id: str) -> None:
    """Converts an already-generated, already-downloaded docx export into
    a PDF via Gotenberg (LibreOffice-backed HTTP microservice, internal
    Docker network only — no internet egress). Heavier retry posture than
    process_export_docx (2 retries, not 1, shorter delay): this is an
    infra/network failure class (Gotenberg unreachable/slow), not a
    content-quality gate."""
    from app.services.gotenberg_client import convert_docx_to_pdf

    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        export = session.get(Export, job.source_entity_id)
        if export is None:
            raise ValueError(f"Export {job.source_entity_id} not found")

        source = session.get(Export, export.derived_from_export_id) if export.derived_from_export_id else None
        if source is None or not source.storage_key:
            raise ValueError(f"Source docx export not found or has no storage_key for pdf export {export.id}")

        docx_bytes = download_file_sync(source.storage_key)
        pdf_bytes = convert_docx_to_pdf(docx_bytes)

        storage_key = f"exports/{uuid.uuid4().hex}.pdf"
        upload_file_sync(pdf_bytes, storage_key, _EXPORT_CONTENT_TYPE_BY_EXT["pdf"])

        export.storage_key = storage_key
        export.file_size = len(pdf_bytes)
        export.status = "completed"
        export.completed_at = datetime.now(timezone.utc)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)

        session.add(AuditEvent(
            user_id=export.user_id,
            entity_type="export",
            entity_id=export.id,
            event_type="export_generated",
            actor_type="system_worker",
            metadata_={"format": "pdf", "derived_from_export_id": source.id},
        ))
        session.commit()

        JOB_THROUGHPUT.labels(job_type="export_pdf", status="completed").inc()
        duration_s = time.monotonic() - t_start
        JOB_DURATION_SECONDS.labels(job_type="export_pdf").observe(duration_s)
        logger.info("export_pdf_complete", job_id=job_id, export_id=export.id, file_size=export.file_size)

    except Exception as e:
        session.rollback()
        duration_s = time.monotonic() - t_start
        logger.error("export_pdf_task_failed", job_id=job_id, error=str(e))
        JOB_THROUGHPUT.labels(job_type="export_pdf", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="export_pdf").observe(duration_s)
        try:
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = str(e)
                job.failed_at = datetime.now(timezone.utc)
            export = session.get(Export, job.source_entity_id) if job is not None else None
            if export is not None:
                export.status = "failed"
                export.error_message = str(e)
            session.commit()
        except Exception as finalize_err:
            logger.error("export_pdf_finalize_failed", job_id=job_id, error=str(finalize_err))
        raise
    finally:
        session.close()


# ──────────────────────────────────────────────────────────────────────
# Sprint 5 / Product Extension #2: Multi-job-post coverage reporting
# ──────────────────────────────────────────────────────────────────────


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    name="app.workers.worker_jobs.process_coverage_report",
    queue="coverage_report",
)
def process_coverage_report(self, job_id: str) -> None:
    """Aggregates match-gap data across every job post in a collection —
    one-shot terminal job, mirrors process_cv_generate/process_match.

    A pure read/aggregation layer: reuses an existing completed MatchRun
    per job post where one exists, runs match_engine.run_match() fresh
    (via _get_or_run_match/_run_and_persist_match — the exact same code
    path process_match itself uses) where one doesn't. Never introduces
    a second, differently-behaved matching engine. A job post with no
    JobPostProfile yet (not finished structuring) is skipped for that
    posting only, recorded in skipped_job_post_ids — never blocks the
    whole report, this codebase's established "never guess, degrade
    gracefully" precedent.
    """
    structlog.contextvars.bind_contextvars(job_id=job_id)
    t_start = time.monotonic()
    session = _get_sync_session()
    try:
        from app.db.models import CoverageReport, JobPostCollection
        from app.services.coverage_aggregation import aggregate_gaps

        job = session.get(ProcessingJob, job_id)
        if job is None:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        session.commit()

        report = session.get(CoverageReport, job.source_entity_id)
        if report is None:
            raise ValueError(f"CoverageReport {job.source_entity_id} not found")

        collection = session.get(JobPostCollection, report.collection_id)
        if collection is None:
            raise ValueError(f"JobPostCollection {report.collection_id} not found")

        match_run_ids: list[str] = []
        skipped_job_post_ids: list[str] = []
        evidence_by_job_post: dict[str, list] = {}

        for job_post_id in collection.job_post_ids or []:
            jp_profile = session.execute(
                select(JobPostProfile).where(JobPostProfile.job_post_id == job_post_id)
            ).scalar_one_or_none()
            if jp_profile is None:
                skipped_job_post_ids.append(job_post_id)
                continue

            match_run = _get_or_run_match(
                session,
                user_id=report.user_id,
                cv_profile_version_id=report.cv_profile_version_id,
                job_post_profile_id=jp_profile.id,
            )
            match_run_ids.append(match_run.id)

            evidence_items = session.execute(
                select(MatchEvidenceItem).where(MatchEvidenceItem.match_run_id == match_run.id)
            ).scalars().all()
            evidence_by_job_post[job_post_id] = evidence_items

        aggregate = aggregate_gaps(
            evidence_by_job_post, total_posts=len(collection.job_post_ids or [])
        )

        report.match_run_ids = match_run_ids or None
        report.aggregate_gaps = aggregate
        report.skipped_job_post_ids = skipped_job_post_ids or None
        report.status = "completed"
        report.completed_at = datetime.now(timezone.utc)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        session.commit()

        JOB_THROUGHPUT.labels(job_type="coverage_report", status="completed").inc()
        duration_s = time.monotonic() - t_start
        JOB_DURATION_SECONDS.labels(job_type="coverage_report").observe(duration_s)
        logger.info(
            "coverage_report_complete",
            job_id=job_id, report_id=report.id,
            gaps=len(aggregate), skipped=len(skipped_job_post_ids),
        )

    except Exception as e:
        session.rollback()
        duration_s = time.monotonic() - t_start
        logger.error("coverage_report_failed", job_id=job_id, error=str(e))
        JOB_THROUGHPUT.labels(job_type="coverage_report", status="failed").inc()
        JOB_DURATION_SECONDS.labels(job_type="coverage_report").observe(duration_s)
        try:
            job = session.get(ProcessingJob, job_id)
            if job is not None:
                job.status = "failed"
                job.last_error = str(e)
                job.failed_at = datetime.now(timezone.utc)
            from app.db.models import CoverageReport
            report = session.get(CoverageReport, job.source_entity_id) if job is not None else None
            if report is not None:
                report.status = "failed"
            session.commit()
        except Exception as finalize_err:
            logger.error("coverage_report_finalize_failed", job_id=job_id, error=str(finalize_err))
        raise
    finally:
        session.close()

