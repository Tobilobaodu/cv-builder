"""Tailored CV generation endpoints — matches 05-openapi.yaml.

POST /matches/{matchId}/tailored-cv, GET/regenerate/approve on
/tailored-cvs/{draftId}. Trial-accessible (RequestIdentity), following
matches.py::create_match()'s manual create-entity-then-job pattern, not
create_processing_job() — a TailoredCvDraft, like a MatchRun, needs to
exist before its worker runs, in the same transaction, not created by
the worker after the fact.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import check_generation_rate_limit, get_client_key
from app.core.security import (
    RequestIdentity,
    get_current_user_or_trial_session,
    get_scoped_session,
    identity_owner_filter,
    ownership_denied,
)
from app.db.models import AuditEvent, MatchRun, ProcessingJob, TailoredCvDraft, TailoredCvSection
from app.schemas.jobs import ProcessingJobRef
from app.schemas.tailored_cv import (
    RegenerateRequest,
    TailoredCvDraftResponse,
    TailoredCvSectionResponse,
    ValidationResultResponse,
)
from app.services.orchestration import enforce_concurrent_job_limit, mark_job_publish_failed
from app.workers.tasks import enqueue_cv_generate

router = APIRouter(tags=["tailored-cvs"])
logger = get_logger(__name__)


def _draft_response(draft: TailoredCvDraft, sections: list[TailoredCvSection]) -> TailoredCvDraftResponse:
    """Explicit construction, not response_model auto-mapping from the ORM
    object — TailoredCvDraft has no 'sections' attribute (a separate,
    FK-related table) for from_attributes to find, and relying on
    auto-mapping for a nested structure like this is exactly the class of
    bug that broke GET /cvs/{cvId}/ats-check earlier this sprint."""
    return TailoredCvDraftResponse(
        id=draft.id,
        match_run_id=draft.match_run_id,
        version_number=draft.version_number,
        status=draft.status,
        sections=[
            TailoredCvSectionResponse(
                id=s.id,
                section_type=s.section_type,
                content_text=s.content_text,
                evidence_references=s.evidence_references,
                generation_task=s.generation_task,
                prompt_version=s.prompt_version,
                model_id=s.model_id,
                validation_status=s.validation_status,
                order_index=s.order_index,
            )
            for s in sorted(sections, key=lambda s: (s.order_index if s.order_index is not None else 0))
        ],
        validation_result=(
            ValidationResultResponse(**draft.validation_result) if draft.validation_result else None
        ),
        improvement_checklist=draft.improvement_checklist,
        created_at=draft.created_at,
        approved_at=draft.approved_at,
    )


async def _create_draft_and_job(
    session: AsyncSession,
    *,
    match_run_id: str,
    version_number: int,
    instructions: str | None,
    identity: RequestIdentity,
) -> tuple[TailoredCvDraft, ProcessingJob]:
    """Shared create-entity-then-job sequence for both the initial POST
    and /regenerate — the only difference between them is which
    match_run_id/version_number/instructions get passed in."""
    draft = TailoredCvDraft(
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        match_run_id=match_run_id,
        version_number=version_number,
        status="pending",
        content_json={},
        instructions=instructions,
    )
    session.add(draft)
    await session.flush()

    await enforce_concurrent_job_limit(session, user_id=identity.user_id, trial_session_id=identity.trial_session_id)

    proc_job = ProcessingJob(
        job_type="cv_generate",
        source_entity_type="tailored_cv_draft",
        source_entity_id=draft.id,
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        status="pending",
    )
    session.add(proc_job)

    session.add(AuditEvent(
        user_id=identity.user_id,
        entity_type="tailored_cv_draft",
        entity_id=draft.id,
        event_type="generate",
        actor_type="user" if identity.user else "trial_session",
    ))

    await session.commit()
    try:
        enqueue_cv_generate(proc_job.id)
    except Exception as e:
        mark_job_publish_failed(proc_job, "Failed to publish task to message broker.")
        await session.commit()
        logger.error("cv_generate_publish_failed", job_id=proc_job.id, error=str(e))
        raise

    return draft, proc_job


# ──────────────────────────────────────────────────────────────────────
# POST /matches/{matchId}/tailored-cv
# ──────────────────────────────────────────────────────────────────────


@router.post("/matches/{matchId}/tailored-cv", status_code=202)
async def create_tailored_cv(
    matchId: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Generate a tailored CV draft from a completed match result.

    Trial-accessible — the free-tier value delivered before the account
    wall. 409 if a draft already exists for this match: subsequent
    versions go through /regenerate, keeping versioning intent explicit
    rather than silently creating version 2 on a repeat POST.
    """
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many tailored CV requests. Please wait and try again.",
        )

    match_result = await session.execute(
        select(MatchRun).where(
            MatchRun.id == matchId,
            identity_owner_filter(MatchRun, identity),
        )
    )
    match_run = match_result.scalar_one_or_none()
    if match_run is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="match_run",
            entity_id=matchId, detail="Match not found",
        )
    if match_run.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Match is in '{match_run.status}' state — must be 'completed' to generate a tailored CV.",
        )

    existing_result = await session.execute(
        select(TailoredCvDraft.id).where(TailoredCvDraft.match_run_id == matchId).limit(1)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A draft already exists for this match. Use /tailored-cvs/{draftId}/regenerate for a new version.",
        )

    draft, proc_job = await _create_draft_and_job(
        session,
        match_run_id=matchId,
        version_number=1,
        instructions=None,
        identity=identity,
    )

    logger.info("tailored_cv_draft_created", draft_id=draft.id, job_id=proc_job.id, match_id=matchId)

    return ProcessingJobRef(job_id=proc_job.id, status="queued")


# ──────────────────────────────────────────────────────────────────────
# GET /tailored-cvs/{draftId}
# ──────────────────────────────────────────────────────────────────────


@router.get("/tailored-cvs/{draftId}", response_model=TailoredCvDraftResponse)
async def get_tailored_cv(
    draftId: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Retrieve a tailored CV draft (IDOR-safe, trial-accessible)."""
    result = await session.execute(
        select(TailoredCvDraft).where(
            TailoredCvDraft.id == draftId,
            identity_owner_filter(TailoredCvDraft, identity),
        )
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="tailored_cv_draft",
            entity_id=draftId, detail="Tailored CV draft not found",
        )

    sections_result = await session.execute(
        select(TailoredCvSection).where(TailoredCvSection.draft_id == draftId)
    )
    sections = sections_result.scalars().all()

    return _draft_response(draft, sections)


# ──────────────────────────────────────────────────────────────────────
# POST /tailored-cvs/{draftId}/regenerate
# ──────────────────────────────────────────────────────────────────────


@router.post("/tailored-cvs/{draftId}/regenerate", status_code=202)
async def regenerate_tailored_cv(
    draftId: str,
    request: Request,
    body: RegenerateRequest,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Generate a new version of a tailored CV draft. The base draft is
    never mutated — old versions stay retrievable by construction, not
    by an archival step."""
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many tailored CV requests. Please wait and try again.",
        )

    result = await session.execute(
        select(TailoredCvDraft).where(
            TailoredCvDraft.id == draftId,
            identity_owner_filter(TailoredCvDraft, identity),
        )
    )
    base_draft = result.scalar_one_or_none()
    if base_draft is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="tailored_cv_draft",
            entity_id=draftId, detail="Tailored CV draft not found",
        )
    if base_draft.status in ("pending", "archived"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot regenerate a draft in '{base_draft.status}' state.",
        )

    draft, proc_job = await _create_draft_and_job(
        session,
        match_run_id=base_draft.match_run_id,
        version_number=base_draft.version_number + 1,
        instructions=body.instructions,
        identity=identity,
    )

    logger.info(
        "tailored_cv_regenerate_created",
        draft_id=draft.id, job_id=proc_job.id, base_draft_id=draftId,
    )

    return ProcessingJobRef(job_id=proc_job.id, status="queued")


# ──────────────────────────────────────────────────────────────────────
# POST /tailored-cvs/{draftId}/approve
# ──────────────────────────────────────────────────────────────────────


@router.post("/tailored-cvs/{draftId}/approve", response_model=TailoredCvDraftResponse)
async def approve_tailored_cv(
    draftId: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Approve a generated draft. No rate limit — no LLM call involved,
    mirrors cover_letters.py::approve()."""
    result = await session.execute(
        select(TailoredCvDraft).where(
            TailoredCvDraft.id == draftId,
            identity_owner_filter(TailoredCvDraft, identity),
        )
    )
    draft = result.scalar_one_or_none()
    if draft is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="tailored_cv_draft",
            entity_id=draftId, detail="Tailored CV draft not found",
        )
    if draft.status not in ("generated", "user_edited"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve a draft in '{draft.status}' state.",
        )

    draft.status = "approved"
    draft.approved_at = datetime.now(timezone.utc)

    session.add(AuditEvent(
        user_id=identity.user_id,
        entity_type="tailored_cv_draft",
        entity_id=draft.id,
        event_type="approve",
        actor_type="user" if identity.user else "trial_session",
    ))

    await session.commit()

    sections_result = await session.execute(
        select(TailoredCvSection).where(TailoredCvSection.draft_id == draftId)
    )
    sections = sections_result.scalars().all()

    return _draft_response(draft, sections)
