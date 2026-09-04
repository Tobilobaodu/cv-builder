"""Cover letter workflow endpoints — Phase 4.

POST /cover-letters/start
GET  /cover-letters/{workflowId}/questions
POST /cover-letters/{workflowId}/answers
GET  /cover-letters/{workflowId}/draft
POST /cover-letters/{workflowId}/regenerate
POST /cover-letters/{workflowId}/approve

Per the non-fabrication rule: unsupported evidence produces a user
question, never an invented claim. Drafts carry non-empty evidence
references.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import check_generation_rate_limit, get_client_key
from app.core.security import get_current_user, get_scoped_session_for_user, ownership_denied
from app.db.models import (
    AuditEvent, CoverLetterAnswer, CoverLetterDraft, CoverLetterQuestion,
    CoverLetterWorkflow, CvFile, CvProfile, CvProfileVersion, JobPost,
    JobPostProfile, MatchEvidenceItem, MatchRun, ProcessingJob, User,
)
from app.schemas.cover_letter import (
    StartWorkflowRequest, CoverLetterWorkflowResponse,
    CoverLetterWorkflowListItem, CoverLetterWorkflowListResponse,
    CoverLetterQuestionResponse, SubmitAnswersRequest,
    CoverLetterDraftResponse,
)
from app.schemas.jobs import ProcessingJobRef
from app.services.cover_letter import generate_questions
from app.services.orchestration import enforce_concurrent_job_limit, mark_job_publish_failed
from app.workers.tasks import enqueue_cover_letter_generate

router = APIRouter(tags=["cover-letters"])
logger = get_logger(__name__)


def _map_workflow(wf: CoverLetterWorkflow) -> CoverLetterWorkflowResponse:
    return CoverLetterWorkflowResponse(
        id=wf.id,
        cvId=wf.cv_profile_version_id,
        jobPostId=wf.job_post_profile_id,
        matchId=wf.match_run_id,
        current_step=wf.current_step,
        status=wf.status,
        question_set_version=wf.question_set_version,
        created_at=wf.created_at,
    )


async def _verify_ownership(
    session: AsyncSession, workflow_id: str, user_id: str,
) -> CoverLetterWorkflow:
    result = await session.execute(
        select(CoverLetterWorkflow).where(
            CoverLetterWorkflow.id == workflow_id,
            CoverLetterWorkflow.user_id == user_id,
        )
    )
    wf = result.scalar_one_or_none()
    if wf is None:
        raise await ownership_denied(
            session, user_id=user_id, entity_type="cover_letter_workflow",
            entity_id=workflow_id, detail="Workflow not found",
        )
    return wf


# ──────────────────────────────────────────────────────────────────────
# POST /cover-letters/start
# ──────────────────────────────────────────────────────────────────────


@router.post("/cover-letters/start", response_model=CoverLetterWorkflowResponse, status_code=201)
async def start_workflow(
    request: Request,
    body: StartWorkflowRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Start a guided cover letter workflow from a CV and job post.

    Rate-limited per client IP (generation tier, see `10-security-plan.md` §9).
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many cover letter requests. Please wait and try again.",
        )

    # Verify CV profile version exists (via current profile pointer),
    # scoped to the caller — without this join, any authenticated user
    # could start a workflow against another user's CV.
    profile_result = await session.execute(
        select(CvProfile)
        .join(CvFile, CvFile.id == CvProfile.cv_file_id)
        .where(
            CvProfile.cv_file_id == body.cvId,
            CvFile.user_id == current_user.id,
        )
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None or profile.current_version_id is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cv_file",
            entity_id=body.cvId, detail="No parsed CV profile found. Process a CV first.",
        )

    # Verify job post profile exists (1:1 with job_posts), scoped to the
    # caller for the same reason.
    jp_result = await session.execute(
        select(JobPostProfile)
        .join(JobPost, JobPost.id == JobPostProfile.job_post_id)
        .where(
            JobPostProfile.job_post_id == body.jobPostId,
            JobPost.user_id == current_user.id,
        )
    )
    jp_profile = jp_result.scalar_one_or_none()
    if jp_profile is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="job_post",
            entity_id=body.jobPostId, detail="Job post not found or not yet structured.",
        )

    # Verify match if provided
    if body.matchId:
        match_result = await session.execute(
            select(MatchRun).where(
                MatchRun.id == body.matchId,
                MatchRun.user_id == current_user.id,
            )
        )
        if match_result.scalar_one_or_none() is None:
            raise await ownership_denied(
                session, user_id=current_user.id, entity_type="match_run",
                entity_id=body.matchId, detail="Match not found",
            )

    # Load CV profile version for name extraction
    cv_version_result = await session.execute(
        select(CvProfileVersion).where(
            CvProfileVersion.id == profile.current_version_id,
        )
    )
    cv_version = cv_version_result.scalar_one_or_none()

    cv_name = None
    if cv_version and cv_version.structured_payload:
        basics = cv_version.structured_payload.get("basics", {}) or {}
        cv_name = basics.get("name")

    # Load match evidence if a match exists
    match_evidence: list[dict] = []
    if body.matchId:
        evidence_result = await session.execute(
            select(MatchEvidenceItem).where(
                MatchEvidenceItem.match_run_id == body.matchId,
            )
        )
        for ei in evidence_result.scalars().all():
            match_evidence.append({
                "id": ei.id,
                "support_level": ei.support_level,
                "requirement_text": ei.requirement_text,
                "requirement_type": ei.requirement_type,
                "suggestion": ei.suggestion,
                "warning": ei.warning,
            })

    # Generate questions
    questions = generate_questions(
        cv_name=cv_name,
        employer_name=jp_profile.employer,
        job_title=jp_profile.job_title or "this role",
        match_evidence=match_evidence,
    )

    # Create workflow
    wf = CoverLetterWorkflow(
        user_id=current_user.id,
        cv_profile_version_id=profile.current_version_id,
        job_post_profile_id=jp_profile.id,
        match_run_id=body.matchId,
        status="awaiting_answers",
        current_step=1,
        total_steps=4,
        question_set_version=1,
    )
    session.add(wf)
    await session.flush()

    # Store questions
    for q in questions:
        session.add(CoverLetterQuestion(
            workflow_id=wf.id,
            step_number=q.step_number,
            question_text=q.question_text,
            question_category=q.question_category,
            required=q.required,
            help_text=q.help_text,
            source_evidence_item_id=q.source_evidence_item_id,
        ))

    # Audit
    session.add(AuditEvent(
        user_id=current_user.id,
        entity_type="cover_letter_workflow",
        entity_id=wf.id,
        event_type="workflow_started",
        actor_type="user",
        ip_address=request.client.host if request.client else None,
    ))

    await session.commit()

    logger.info("cover_letter_workflow_started", workflow_id=wf.id, user_id=current_user.id)

    return _map_workflow(wf)


# ──────────────────────────────────────────────────────────────────────
# GET /cover-letters — list
# ──────────────────────────────────────────────────────────────────────


@router.get("/cover-letters", response_model=CoverLetterWorkflowListResponse)
async def list_cover_letter_workflows(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """List cover-letter workflows for the current user, with pagination.

    Account-only — cover letters have always been get_current_user-only
    throughout this router, matching GET /cvs and GET /job-posts'
    precedent for their own list endpoints.
    """
    total = (
        await session.execute(
            select(func.count())
            .select_from(CoverLetterWorkflow)
            .where(
                CoverLetterWorkflow.user_id == current_user.id,
                CoverLetterWorkflow.status != "archived",
            )
        )
    ).scalar_one()

    result = await session.execute(
        select(CoverLetterWorkflow, JobPostProfile)
        .join(JobPostProfile, JobPostProfile.id == CoverLetterWorkflow.job_post_profile_id)
        .where(
            CoverLetterWorkflow.user_id == current_user.id,
            CoverLetterWorkflow.status != "archived",
        )
        .order_by(CoverLetterWorkflow.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = result.all()

    items = [
        CoverLetterWorkflowListItem(
            id=wf.id,
            job_post_id=profile.job_post_id,
            job_title=profile.job_title,
            employer=profile.employer,
            status=wf.status,
            current_step=wf.current_step,
            total_steps=wf.total_steps,
            created_at=wf.created_at,
        )
        for wf, profile in rows
    ]

    return CoverLetterWorkflowListResponse(items=items, total=total, limit=limit, offset=offset)


# ──────────────────────────────────────────────────────────────────────
# GET /cover-letters/{workflowId}/questions
# ──────────────────────────────────────────────────────────────────────


@router.get("/cover-letters/{workflowId}/questions",
            response_model=list[CoverLetterQuestionResponse])
async def get_questions(
    workflowId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Return the question set for the current step."""
    wf = await _verify_ownership(session, workflowId, current_user.id)

    result = await session.execute(
        select(CoverLetterQuestion).where(
            CoverLetterQuestion.workflow_id == wf.id,
            CoverLetterQuestion.step_number == wf.current_step,
        ).order_by(CoverLetterQuestion.created_at)
    )
    questions = result.scalars().all()

    return [
        CoverLetterQuestionResponse(
            id=q.id,
            step_number=q.step_number,
            question_text=q.question_text,
            question_category=q.question_category,
        )
        for q in questions
    ]


# ──────────────────────────────────────────────────────────────────────
# POST /cover-letters/{workflowId}/answers
# ──────────────────────────────────────────────────────────────────────


@router.post("/cover-letters/{workflowId}/answers",
             response_model=CoverLetterWorkflowResponse, status_code=202)
async def submit_answers(
    workflowId: str,
    body: SubmitAnswersRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Submit answers for the current step."""
    wf = await _verify_ownership(session, workflowId, current_user.id)

    if wf.status != "awaiting_answers":
        raise HTTPException(
            status_code=409,
            detail=f"Workflow is in '{wf.status}' state, not awaiting answers.",
        )

    answered_question_ids: set[str] = set()
    for answer_item in body.answers:
        # Verify the question belongs to this workflow at the current step
        q_result = await session.execute(
            select(CoverLetterQuestion).where(
                CoverLetterQuestion.id == answer_item.questionId,
                CoverLetterQuestion.workflow_id == wf.id,
                CoverLetterQuestion.step_number == wf.current_step,
            )
        )
        question = q_result.scalar_one_or_none()
        if question is None:
            raise HTTPException(
                status_code=404,
                detail=f"Question {answer_item.questionId} not found for current step",
            )

        session.add(CoverLetterAnswer(
            workflow_id=wf.id,
            question_id=question.id,
            answer_text=answer_item.answerText,
        ))
        answered_question_ids.add(question.id)

    # Every required question at this step must have an answer in this
    # request — 09-test-plan.md §7: "a required question left unanswered
    # is handled explicitly... rather than silently proceeding as if it
    # were answered." Previously unenforced anywhere in this codebase.
    step_questions_result = await session.execute(
        select(CoverLetterQuestion).where(
            CoverLetterQuestion.workflow_id == wf.id,
            CoverLetterQuestion.step_number == wf.current_step,
            CoverLetterQuestion.required.is_(True),
        )
    )
    missing_required = [
        q.id for q in step_questions_result.scalars().all()
        if q.id not in answered_question_ids
    ]
    if missing_required:
        raise HTTPException(
            status_code=422,
            detail=f"Required question(s) not answered: {missing_required}",
        )

    # Advance step
    if wf.current_step < wf.total_steps:
        wf.current_step += 1
        await session.commit()
    else:
        # All steps complete — dispatch async draft generation
        wf.status = "generating"
        await session.commit()
        await _create_draft_and_generation_job(session, wf, current_user)

    logger.info("workflow_answers_submitted", workflow_id=wf.id, step=wf.current_step)

    return _map_workflow(wf)


# ──────────────────────────────────────────────────────────────────────
# GET /cover-letters/{workflowId}/draft
# ──────────────────────────────────────────────────────────────────────


@router.get("/cover-letters/{workflowId}/draft",
            response_model=CoverLetterDraftResponse)
async def get_draft(
    workflowId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Return the current draft of the cover letter."""
    wf = await _verify_ownership(session, workflowId, current_user.id)

    result = await session.execute(
        select(CoverLetterDraft).where(
            CoverLetterDraft.workflow_id == wf.id,
        ).order_by(CoverLetterDraft.version_number.desc()).limit(1)
    )
    draft = result.scalar_one_or_none()
    # A draft row now exists immediately on submit/regenerate (status
    # "pending"), before the worker has actually generated anything —
    # under the old synchronous flow `draft is None` was sufficient;
    # under async, an empty-but-existing pending draft must also 404,
    # or this endpoint would return 200 with an empty body mid-generation.
    if draft is None or draft.status == "pending":
        raise HTTPException(status_code=404, detail="No draft yet — answer all questions first.")

    return CoverLetterDraftResponse(
        id=draft.id,
        workflow_id=draft.workflow_id,
        version_number=draft.version_number,
        status=draft.status,
        body_text=draft.body_text,
        evidence_references=draft.evidence_references,
        prompt_version=draft.prompt_version,
        model_id=draft.model_id,
        created_at=draft.created_at.isoformat() if draft.created_at else "",
        approved_at=draft.approved_at.isoformat() if draft.approved_at else None,
    )


# ──────────────────────────────────────────────────────────────────────
# POST /cover-letters/{workflowId}/regenerate
# ──────────────────────────────────────────────────────────────────────


@router.post("/cover-letters/{workflowId}/regenerate", status_code=202,
             response_model=ProcessingJobRef)
async def regenerate(
    request: Request,
    workflowId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Regenerate the letter (after user edits or new answers).

    Rate-limited per client IP (generation tier).
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many cover letter requests. Please wait and try again.",
        )

    wf = await _verify_ownership(session, workflowId, current_user.id)
    # "generation_failed" is included so a genuinely failed generation
    # doesn't permanently lock the workflow out of retrying — the same
    # "dead end" class of bug as a queue with no consuming worker.
    if wf.status not in ("draft_ready", "approved", "generation_failed"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot regenerate in '{wf.status}' state.",
        )

    wf.status = "generating"
    await session.commit()
    draft, proc_job = await _create_draft_and_generation_job(session, wf, current_user)

    return ProcessingJobRef(job_id=proc_job.id, status="queued")


# ──────────────────────────────────────────────────────────────────────
# POST /cover-letters/{workflowId}/approve
# ──────────────────────────────────────────────────────────────────────


@router.post("/cover-letters/{workflowId}/approve",
             response_model=CoverLetterDraftResponse)
async def approve(
    workflowId: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Mark the current draft as approved."""
    wf = await _verify_ownership(session, workflowId, current_user.id)

    if wf.status != "draft_ready":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve in '{wf.status}' state — wait for draft generation.",
        )

    # Get latest draft
    result = await session.execute(
        select(CoverLetterDraft).where(
            CoverLetterDraft.workflow_id == wf.id,
        ).order_by(CoverLetterDraft.version_number.desc()).limit(1)
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="No draft to approve.")

    draft.status = "approved"
    draft.approved_at = datetime.now(timezone.utc)
    wf.status = "approved"
    wf.approved_at = datetime.now(timezone.utc)

    session.add(AuditEvent(
        user_id=current_user.id,
        entity_type="cover_letter_workflow",
        entity_id=wf.id,
        event_type="letter_approved",
        actor_type="user",
        ip_address=request.client.host if request.client else None,
    ))

    await session.commit()

    logger.info("cover_letter_approved", workflow_id=wf.id)

    return CoverLetterDraftResponse(
        id=draft.id,
        workflow_id=draft.workflow_id,
        version_number=draft.version_number,
        status=draft.status,
        body_text=draft.body_text,
        evidence_references=draft.evidence_references,
        prompt_version=draft.prompt_version,
        model_id=draft.model_id,
        created_at=draft.created_at.isoformat() if draft.created_at else "",
        approved_at=draft.approved_at.isoformat() if draft.approved_at else None,
    )


# ──────────────────────────────────────────────────────────────────────
# DELETE /cover-letters/{workflowId}
# ──────────────────────────────────────────────────────────────────────


@router.delete("/cover-letters/{workflowId}", status_code=202)
async def delete_cover_letter_workflow(
    workflowId: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    """Archive a cover-letter workflow. Sets status="archived" rather than
    a deleted_at column — unlike job_posts/cvs/match_runs, this model has
    no soft-delete column, but "archived" was already a documented status
    value in this table's own status comment (never actually set by any
    code path until now) — reusing it keeps this a status transition, not
    a new migration, and list_cover_letter_workflows above already
    excludes it."""
    wf = await _verify_ownership(session, workflowId, current_user.id)

    wf.status = "archived"

    session.add(AuditEvent(
        user_id=current_user.id,
        entity_type="cover_letter_workflow",
        entity_id=wf.id,
        event_type="deletion_requested",
        actor_type="user",
    ))

    await session.commit()
    logger.info("cover_letter_workflow_archived", workflow_id=wf.id, user_id=current_user.id)


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────


async def _create_draft_and_generation_job(
    session: AsyncSession, wf: CoverLetterWorkflow, user: User,
) -> tuple[CoverLetterDraft, ProcessingJob]:
    """Shared create-entity-then-job sequence for both submit_answers'
    final step and /regenerate — mirrors tailored_cvs.py's
    _create_draft_and_job exactly. The draft row is created empty
    (status="pending") in the same transaction as the ProcessingJob, so
    the worker (process_cover_letter_generate) has something to update
    rather than something to create from scratch — same reasoning as
    Sprint 3's TailoredCvDraft.

    Cover letters stay get_current_user-only (no trial_session_id) —
    deliberate, per the product-vision account-paywall boundary; the one
    place this sprint does NOT mirror Sprint 3's trial-accessible pattern.
    """
    max_ver_result = await session.execute(
        select(CoverLetterDraft.version_number).where(
            CoverLetterDraft.workflow_id == wf.id,
        ).order_by(CoverLetterDraft.version_number.desc()).limit(1)
    )
    max_ver = max_ver_result.scalar() or 0

    draft = CoverLetterDraft(
        workflow_id=wf.id,
        version_number=max_ver + 1,
        status="pending",
        body_text="",
    )
    session.add(draft)
    await session.flush()

    await enforce_concurrent_job_limit(session, user_id=user.id)

    proc_job = ProcessingJob(
        job_type="cover_letter_generate",
        source_entity_type="cover_letter_draft",
        source_entity_id=draft.id,
        user_id=user.id,
        status="pending",
    )
    session.add(proc_job)

    session.add(AuditEvent(
        user_id=user.id,
        entity_type="cover_letter_draft",
        entity_id=draft.id,
        event_type="generate",
        actor_type="user",
    ))

    await session.commit()
    try:
        enqueue_cover_letter_generate(proc_job.id)
    except Exception as e:
        mark_job_publish_failed(proc_job, "Failed to publish task to message broker.")
        await session.commit()
        logger.error("cover_letter_generate_publish_failed", job_id=proc_job.id, error=str(e))
        raise

    return draft, proc_job