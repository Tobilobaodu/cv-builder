"""Export endpoints — Sprint 5.

POST /exports/cv/{draftId}
POST /exports/cover-letter/{workflowId}
POST /exports/application-pack
GET  /exports/templates
GET  /exports/{exportId}
GET  /exports/{exportId}/download
POST /exports/{exportId}/pdf

Only the three POST-to-create endpoints were documented in the original
05-openapi.yaml — GET /exports/{exportId}, .../download, and .../pdf are
new surface this sprint adds because the demo criterion ("upload through
to a downloaded application pack") is unreachable without a polling and
download mechanism, same "found a gap the spec missed, fixed it in
scope" spirit as prior sprints. No presigned URL: this codebase has zero
existing presigned-URL usage and consistently re-checks ownership on
every read rather than trusting a bearer-style link, so downloads are
proxied through this API and checked every request instead.

DOCX is the primary export format, with multiple ATS-ready template
layouts to pick from (export_templates.py). PDF is secondary and is only
ever a conversion of an *already-downloaded* docx export — never an
independent render — enforced via Export.downloaded_at/derived_from_export_id.

POST /exports/cv/{draftId} is trial-accessible (tailored CV generation
itself already is, end to end); the cover-letter and application-pack
exports stay account-only, since both require a CoverLetterWorkflow,
which is itself account-only.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.rate_limit import check_generation_rate_limit, get_client_key
from app.core.security import (
    RequestIdentity,
    get_current_user,
    get_current_user_or_trial_session,
    get_scoped_session,
    get_scoped_session_for_user,
    identity_owner_filter,
    ownership_denied,
)
from app.core.storage import download_file
from app.db.models import (
    AuditEvent,
    CoverLetterDraft,
    CoverLetterWorkflow,
    Export,
    ProcessingJob,
    TailoredCvDraft,
    User,
)
from app.schemas.export import (
    ApplicationPackExportRequest,
    CreateExportRequest,
    ExportRequestOut,
    ExportTemplateOut,
)
from app.services import export_templates
from app.services.orchestration import enforce_concurrent_job_limit, mark_job_publish_failed
from app.workers.tasks import enqueue_export, enqueue_export_pdf

router = APIRouter(tags=["exports"])
logger = get_logger(__name__)

_STATUS_MAP = {"pending": "queued", "processing": "processing", "completed": "completed", "failed": "failed"}
_SOURCE_DRAFT_TYPE_MAP = {"cv": "tailored_cv", "cover_letter": "cover_letter", "application_pack": "application_pack"}
_CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "zip": "application/zip",
}


def _export_response(export: Export) -> ExportRequestOut:
    return ExportRequestOut(
        id=export.id,
        status=_STATUS_MAP.get(export.status, export.status),
        source_draft_type=_SOURCE_DRAFT_TYPE_MAP.get(export.export_type, export.export_type),
        file_reference=f"/api/v1/exports/{export.id}/download" if export.status == "completed" else None,
        format=export.format,
        template_id=export.template_id,
        downloaded_at=export.downloaded_at,
        derived_from_export_id=export.derived_from_export_id,
        error_message=export.error_message,
        created_at=export.created_at,
    )


async def _create_export_and_job(
    session: AsyncSession,
    *,
    export_type: str,
    source_id: str,
    secondary_source_id: str | None,
    template_id: str | None,
    identity: RequestIdentity,
    format: str = "docx",
    job_type: str = "export",
    derived_from_export_id: str | None = None,
    enqueue_fn=enqueue_export,
) -> tuple[Export, ProcessingJob]:
    """Shared create-entity-then-job sequence, mirroring
    tailored_cvs.py::_create_draft_and_job / cover_letters.py::
    _create_draft_and_generation_job exactly. enqueue_fn/job_type are
    parameterized (not hardcoded) so the PDF-conversion path
    (POST /exports/{exportId}/pdf) can reuse this instead of duplicating
    it with the wrong queue — docx generation and PDF conversion are
    dispatched to two different Celery queues (export vs export_pdf)."""
    export = Export(
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        export_type=export_type,
        source_id=source_id,
        secondary_source_id=secondary_source_id,
        format=format,
        template_id=template_id,
        status="pending",
        derived_from_export_id=derived_from_export_id,
    )
    session.add(export)
    await session.flush()

    await enforce_concurrent_job_limit(
        session, user_id=identity.user_id, trial_session_id=identity.trial_session_id
    )

    proc_job = ProcessingJob(
        job_type=job_type,
        source_entity_type="export",
        source_entity_id=export.id,
        user_id=identity.user_id,
        trial_session_id=identity.trial_session_id,
        status="pending",
    )
    session.add(proc_job)

    session.add(AuditEvent(
        user_id=identity.user_id,
        entity_type="export",
        entity_id=export.id,
        event_type="export_requested",
        actor_type="user" if identity.user else "trial_session",
    ))

    await session.commit()
    try:
        enqueue_fn(proc_job.id)
    except Exception as e:
        mark_job_publish_failed(proc_job, "Failed to publish task to message broker.")
        await session.commit()
        logger.error("export_publish_failed", job_id=proc_job.id, error=str(e))
        raise

    return export, proc_job


# ──────────────────────────────────────────────────────────────────────
# GET /exports/templates — registered before /exports/{exportId} so the
# literal "templates" segment isn't shadowed by the dynamic path param.
# ──────────────────────────────────────────────────────────────────────


@router.get("/exports/templates", response_model=list[ExportTemplateOut])
async def list_export_templates():
    return [ExportTemplateOut(**t) for t in export_templates.list_templates()]


# ──────────────────────────────────────────────────────────────────────
# POST /exports/cv/{draftId}
# ──────────────────────────────────────────────────────────────────────


@router.post("/exports/cv/{draftId}", response_model=ExportRequestOut, status_code=202)
async def export_cv(
    draftId: str,
    request: Request,
    body: CreateExportRequest,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many export requests. Please wait and try again.",
        )

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
    if draft.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Draft is in '{draft.status}' state — must be approved before export.",
        )

    template_id = export_templates.resolve_cv_template_id(body.template_id)
    export, proc_job = await _create_export_and_job(
        session, export_type="cv", source_id=draftId, secondary_source_id=None,
        template_id=template_id, identity=identity,
    )
    logger.info("export_created", export_id=export.id, job_id=proc_job.id, export_type="cv")
    return _export_response(export)


# ──────────────────────────────────────────────────────────────────────
# POST /exports/cover-letter/{workflowId}
# ──────────────────────────────────────────────────────────────────────


@router.post("/exports/cover-letter/{workflowId}", response_model=ExportRequestOut, status_code=202)
async def export_cover_letter(
    workflowId: str,
    request: Request,
    body: CreateExportRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many export requests. Please wait and try again.",
        )

    result = await session.execute(
        select(CoverLetterWorkflow).where(
            CoverLetterWorkflow.id == workflowId,
            CoverLetterWorkflow.user_id == current_user.id,
        )
    )
    wf = result.scalar_one_or_none()
    if wf is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="cover_letter_workflow",
            entity_id=workflowId, detail="Workflow not found",
        )
    if wf.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Workflow is in '{wf.status}' state — the letter must be approved before export.",
        )

    draft_result = await session.execute(
        select(CoverLetterDraft).where(
            CoverLetterDraft.workflow_id == wf.id,
        ).order_by(CoverLetterDraft.version_number.desc()).limit(1)
    )
    draft = draft_result.scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=404, detail="No approved draft found for this workflow.")

    identity = RequestIdentity(user=current_user, trial_session=None)
    export, proc_job = await _create_export_and_job(
        session, export_type="cover_letter", source_id=draft.id, secondary_source_id=None,
        template_id=None, identity=identity,
    )
    logger.info("export_created", export_id=export.id, job_id=proc_job.id, export_type="cover_letter")
    return _export_response(export)


# ──────────────────────────────────────────────────────────────────────
# POST /exports/application-pack
# ──────────────────────────────────────────────────────────────────────


@router.post("/exports/application-pack", response_model=ExportRequestOut, status_code=202)
async def export_application_pack(
    request: Request,
    body: ApplicationPackExportRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_scoped_session_for_user),
):
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many export requests. Please wait and try again.",
        )

    cv_result = await session.execute(
        select(TailoredCvDraft).where(
            TailoredCvDraft.id == body.tailored_cv_draft_id,
            TailoredCvDraft.user_id == current_user.id,
        )
    )
    cv_draft = cv_result.scalar_one_or_none()

    wf_result = await session.execute(
        select(CoverLetterWorkflow).where(
            CoverLetterWorkflow.id == body.cover_letter_workflow_id,
            CoverLetterWorkflow.user_id == current_user.id,
        )
    )
    wf = wf_result.scalar_one_or_none()

    if cv_draft is None or wf is None:
        raise await ownership_denied(
            session, user_id=current_user.id, entity_type="tailored_cv_draft",
            entity_id=body.tailored_cv_draft_id, detail="Tailored CV draft or cover letter workflow not found.",
        )
    if cv_draft.status != "approved" or wf.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Both the tailored CV draft and the cover letter must be approved before an application-pack export.",
        )

    cl_draft_result = await session.execute(
        select(CoverLetterDraft).where(
            CoverLetterDraft.workflow_id == wf.id,
        ).order_by(CoverLetterDraft.version_number.desc()).limit(1)
    )
    cl_draft = cl_draft_result.scalar_one_or_none()
    if cl_draft is None:
        raise HTTPException(status_code=404, detail="No approved cover letter draft found.")

    template_id = export_templates.resolve_cv_template_id(body.template_id)
    identity = RequestIdentity(user=current_user, trial_session=None)
    export, proc_job = await _create_export_and_job(
        session, export_type="application_pack", source_id=cv_draft.id, secondary_source_id=cl_draft.id,
        template_id=template_id, identity=identity, format="zip",
    )
    logger.info("export_created", export_id=export.id, job_id=proc_job.id, export_type="application_pack")
    return _export_response(export)


# ──────────────────────────────────────────────────────────────────────
# GET /exports/{exportId}
# ──────────────────────────────────────────────────────────────────────


@router.get("/exports/{exportId}", response_model=ExportRequestOut)
async def get_export(
    exportId: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    result = await session.execute(
        select(Export).where(Export.id == exportId, identity_owner_filter(Export, identity))
    )
    export = result.scalar_one_or_none()
    if export is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="export",
            entity_id=exportId, detail="Export not found",
        )
    return _export_response(export)


# ──────────────────────────────────────────────────────────────────────
# GET /exports/{exportId}/download
# ──────────────────────────────────────────────────────────────────────


@router.get("/exports/{exportId}/download")
async def download_export(
    exportId: str,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """Proxied download, ownership-checked on every request — sets
    downloaded_at the first time only, which is what gates PDF
    conversion becoming available (POST /exports/{exportId}/pdf)."""
    result = await session.execute(
        select(Export).where(Export.id == exportId, identity_owner_filter(Export, identity))
    )
    export = result.scalar_one_or_none()
    if export is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="export",
            entity_id=exportId, detail="Export not found",
        )
    if export.status != "completed" or not export.storage_key:
        raise HTTPException(
            status_code=409,
            detail=f"Export is in '{export.status}' state — not ready to download yet.",
        )

    if export.downloaded_at is None:
        export.downloaded_at = datetime.now(timezone.utc)
        await session.commit()

    file_bytes = await download_file(export.storage_key)
    content_type = _CONTENT_TYPES.get(export.format, "application/octet-stream")
    filename = f"{export.export_type}_{export.id}.{export.format}"
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ──────────────────────────────────────────────────────────────────────
# POST /exports/{exportId}/pdf
# ──────────────────────────────────────────────────────────────────────


@router.post("/exports/{exportId}/pdf", response_model=ExportRequestOut, status_code=202)
async def export_pdf(
    exportId: str,
    request: Request,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
    session: AsyncSession = Depends(get_scoped_session),
):
    """PDF is always a conversion of an already-downloaded docx export —
    never an independent render. Every precondition below 409s with its
    own specific message, rather than one generic 'not eligible' error."""
    client_key = get_client_key(request)
    if not check_generation_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many export requests. Please wait and try again.",
        )

    result = await session.execute(
        select(Export).where(Export.id == exportId, identity_owner_filter(Export, identity))
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise await ownership_denied(
            session, user_id=identity.user_id, entity_type="export",
            entity_id=exportId, detail="Export not found",
        )
    if source.format != "docx":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot convert a '{source.format}' export to PDF — PDF conversion is only available for docx exports.",
        )
    if source.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Export is in '{source.status}' state — must be 'completed' first.",
        )
    if source.downloaded_at is None:
        raise HTTPException(
            status_code=409,
            detail="Download the docx export first — PDF conversion only becomes available after that.",
        )

    export, proc_job = await _create_export_and_job(
        session, export_type=source.export_type, source_id=source.source_id,
        secondary_source_id=source.secondary_source_id, template_id=source.template_id,
        identity=identity, format="pdf", job_type="export_pdf",
        derived_from_export_id=source.id, enqueue_fn=enqueue_export_pdf,
    )

    logger.info("export_pdf_created", export_id=export.id, job_id=proc_job.id, source_export_id=source.id)
    return _export_response(export)
