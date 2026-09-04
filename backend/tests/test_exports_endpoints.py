"""Live-DB tests for the export endpoints (Sprint 5).

Mirrors test_tailored_cv_endpoints.py's / test_cover_letters_endpoints.py's
pattern exactly: own create_async_engine(..., poolclass=NullPool), no
conftest.py, call the route functions directly (async, no TestClient).
Does NOT invoke the Celery worker, MinIO, or a real Gotenberg container
— process_export_docx/process_export_pdf's own rendering logic is
covered by test_export_rendering.py/test_export_pdf_conversion.py; this
file proves the DB wiring, ownership split (trial-accessible for cv
exports, account-only for cover-letter/application-pack), the approval
gate, and the docx-download-gates-pdf-conversion sequencing.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import RequestIdentity
from app.api.v1.exports import (
    export_application_pack, export_cover_letter, export_cv, export_pdf,
    get_export, list_export_templates,
)
from app.schemas.export import ApplicationPackExportRequest, CreateExportRequest
from app.db.models import (
    CoverLetterDraft, CoverLetterWorkflow, CvFile, CvProfileVersion, Export,
    JobPost, JobPostProfile, MatchRun, ProcessingJob, TailoredCvDraft,
    TrialSession, User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)

_ip_counter = 0


def _fake_request():
    global _ip_counter
    _ip_counter += 1
    return SimpleNamespace(client=SimpleNamespace(host=f"10.79.0.{_ip_counter}"), headers={})


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


async def _trial_session(session):
    ts = TrialSession(id=str(uuid.uuid4()), expires_at=datetime.now(timezone.utc) + timedelta(hours=48))
    session.add(ts)
    await session.flush()
    return ts


async def _tailored_cv_draft(session, *, user_id=None, trial_session_id=None, status="approved"):
    owner = {"user_id": user_id, "trial_session_id": trial_session_id}
    cv_file = CvFile(
        id=str(uuid.uuid4()), filename="cv.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="parsed", **owner,
    )
    session.add(cv_file)
    await session.flush()
    pv = CvProfileVersion(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, version_number=1,
        profile_hash=uuid.uuid4().hex, schema_version="1.0",
        structured_payload={"basics": {"name": None}}, **owner,
    )
    session.add(pv)
    await session.flush()
    job_post = JobPost(
        id=str(uuid.uuid4()), source_type="text", raw_text="Python engineer wanted",
        status="structured", **owner,
    )
    session.add(job_post)
    await session.flush()
    jp_profile = JobPostProfile(id=str(uuid.uuid4()), job_post_id=job_post.id, required_skills=["Python"])
    session.add(jp_profile)
    await session.flush()
    match_run = MatchRun(
        id=str(uuid.uuid4()), cv_profile_version_id=pv.id, job_post_profile_id=jp_profile.id,
        status="completed", **owner,
    )
    session.add(match_run)
    await session.flush()
    draft = TailoredCvDraft(
        id=str(uuid.uuid4()), match_run_id=match_run.id, version_number=1,
        status=status, content_json={}, **owner,
    )
    session.add(draft)
    await session.flush()
    return draft


async def _cover_letter_workflow_and_draft(session, user, *, wf_status="approved", draft_status="approved"):
    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=user.id, filename="cv.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="parsed",
    )
    session.add(cv_file)
    await session.flush()
    pv = CvProfileVersion(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, user_id=user.id, version_number=1,
        profile_hash=uuid.uuid4().hex, schema_version="1.0", structured_payload={"basics": {}},
    )
    session.add(pv)
    job_post = JobPost(
        id=str(uuid.uuid4()), user_id=user.id, source_type="text",
        raw_text="Python engineer wanted" * 5, status="structured",
    )
    session.add(job_post)
    await session.flush()
    jp_profile = JobPostProfile(id=str(uuid.uuid4()), job_post_id=job_post.id, employer="Acme")
    session.add(jp_profile)
    await session.flush()
    wf = CoverLetterWorkflow(
        id=str(uuid.uuid4()), user_id=user.id, cv_profile_version_id=pv.id,
        job_post_profile_id=jp_profile.id, status=wf_status, current_step=3, total_steps=3,
        question_set_version=1,
    )
    session.add(wf)
    await session.flush()
    draft = CoverLetterDraft(
        id=str(uuid.uuid4()), workflow_id=wf.id, version_number=1,
        status=draft_status, body_text="Dear Hiring Manager, ...",
    )
    session.add(draft)
    await session.flush()
    return wf, draft


async def _export(session, *, user_id=None, trial_session_id=None, export_type="cv",
                   format="docx", status="completed", storage_key=None, downloaded_at=None,
                   derived_from_export_id=None, source_id=None):
    e = Export(
        id=str(uuid.uuid4()), user_id=user_id, trial_session_id=trial_session_id,
        export_type=export_type, source_id=source_id or str(uuid.uuid4()),
        format=format, status=status, storage_key=storage_key, downloaded_at=downloaded_at,
        derived_from_export_id=derived_from_export_id,
    )
    session.add(e)
    await session.flush()
    return e


# ═══════════════════════════════════════════════════════════════════════
# POST /exports/cv/{draftId}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_export_cv_creates_pending_export_and_job():
    async with _test_session_factory() as s:
        user = await _user(s, "cv1")
        draft = await _tailored_cv_draft(s, user_id=user.id, status="approved")
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        result = await export_cv(
            draftId=draft_id, request=_fake_request(),
            body=CreateExportRequest(template_id="compact"), identity=identity, session=s,
        )
        assert result.status == "queued"
        assert result.template_id == "compact"

    async with _test_session_factory() as verify_s:
        export_result = await verify_s.execute(select(Export).where(Export.source_id == draft_id))
        export = export_result.scalar_one()
        assert export.status == "pending"
        assert export.export_type == "cv"
        assert export.format == "docx"

        job_result = await verify_s.execute(
            select(ProcessingJob).where(ProcessingJob.source_entity_id == export.id)
        )
        job = job_result.scalar_one()
        assert job.job_type == "export"
        assert job.source_entity_type == "export"


@pytest.mark.asyncio(loop_scope="function")
async def test_export_cv_rejects_unapproved_draft():
    async with _test_session_factory() as s:
        user = await _user(s, "cv2")
        draft = await _tailored_cv_draft(s, user_id=user.id, status="generated")
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await export_cv(
                draftId=draft_id, request=_fake_request(),
                body=CreateExportRequest(), identity=identity, session=s,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="function")
async def test_export_cv_trial_session_can_create_and_read():
    async with _test_session_factory() as s:
        ts = await _trial_session(s)
        draft = await _tailored_cv_draft(s, trial_session_id=ts.id, status="approved")
        await s.commit()
        draft_id, ts_id = draft.id, ts.id

    async with _test_session_factory() as s:
        ts_row = await s.get(TrialSession, ts_id)
        identity = RequestIdentity(user=None, trial_session=ts_row)
        result = await export_cv(
            draftId=draft_id, request=_fake_request(),
            body=CreateExportRequest(), identity=identity, session=s,
        )
        assert result.status == "queued"

    async with _test_session_factory() as verify_s:
        export_result = await verify_s.execute(select(Export).where(Export.source_id == draft_id))
        export = export_result.scalar_one()
        assert export.trial_session_id == ts_id
        assert export.user_id is None
        export_id = export.id

    async with _test_session_factory() as s:
        ts_row = await s.get(TrialSession, ts_id)
        identity = RequestIdentity(user=None, trial_session=ts_row)
        response = await get_export(exportId=export_id, identity=identity, session=s)
        assert response.id == export_id


@pytest.mark.asyncio(loop_scope="function")
async def test_export_cv_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "cv4owner")
        draft = await _tailored_cv_draft(s, user_id=owner.id, status="approved")
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "cv4attacker")
        await s.commit()

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=attacker, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await export_cv(
                draftId=draft_id, request=_fake_request(),
                body=CreateExportRequest(), identity=identity, session=s,
            )
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /exports/cover-letter/{workflowId}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_export_cover_letter_creates_pending_export():
    async with _test_session_factory() as s:
        user = await _user(s, "cl1")
        wf, draft = await _cover_letter_workflow_and_draft(s, user)
        await s.commit()
        wf_id, draft_id = wf.id, draft.id

    async with _test_session_factory() as s:
        result = await export_cover_letter(
            workflowId=wf_id, request=_fake_request(),
            body=CreateExportRequest(), current_user=user, session=s,
        )
        assert result.status == "queued"

    async with _test_session_factory() as verify_s:
        export_result = await verify_s.execute(select(Export).where(Export.source_id == draft_id))
        export = export_result.scalar_one()
        assert export.export_type == "cover_letter"
        assert export.trial_session_id is None


@pytest.mark.asyncio(loop_scope="function")
async def test_export_cover_letter_rejects_unapproved_workflow():
    async with _test_session_factory() as s:
        user = await _user(s, "cl2")
        wf, draft = await _cover_letter_workflow_and_draft(s, user, wf_status="draft_ready")
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await export_cover_letter(
                workflowId=wf_id, request=_fake_request(),
                body=CreateExportRequest(), current_user=user, session=s,
            )
        assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# POST /exports/application-pack
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_application_pack_requires_both_approved():
    async with _test_session_factory() as s:
        user = await _user(s, "pack1")
        cv_draft = await _tailored_cv_draft(s, user_id=user.id, status="generated")  # not approved
        wf, cl_draft = await _cover_letter_workflow_and_draft(s, user, wf_status="approved")
        await s.commit()
        cv_draft_id, wf_id = cv_draft.id, wf.id

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await export_application_pack(
                request=_fake_request(),
                body=ApplicationPackExportRequest(
                    tailored_cv_draft_id=cv_draft_id, cover_letter_workflow_id=wf_id,
                ),
                current_user=user, session=s,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="function")
async def test_application_pack_creates_zip_format_export():
    async with _test_session_factory() as s:
        user = await _user(s, "pack2")
        cv_draft = await _tailored_cv_draft(s, user_id=user.id, status="approved")
        wf, cl_draft = await _cover_letter_workflow_and_draft(s, user, wf_status="approved")
        await s.commit()
        cv_draft_id, wf_id = cv_draft.id, wf.id

    async with _test_session_factory() as s:
        result = await export_application_pack(
            request=_fake_request(),
            body=ApplicationPackExportRequest(
                tailored_cv_draft_id=cv_draft_id, cover_letter_workflow_id=wf_id,
            ),
            current_user=user, session=s,
        )
        assert result.status == "queued"
        assert result.format == "zip"


# ═══════════════════════════════════════════════════════════════════════
# POST /exports/{exportId}/pdf — the docx-downloaded gate
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_pdf_rejected_before_docx_downloaded():
    async with _test_session_factory() as s:
        user = await _user(s, "pdf1")
        export = await _export(
            s, user_id=user.id, format="docx", status="completed",
            storage_key="exports/fake.docx", downloaded_at=None,
        )
        await s.commit()
        export_id = export.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await export_pdf(exportId=export_id, request=_fake_request(), identity=identity, session=s)
        assert exc.value.status_code == 409
        assert "download" in exc.value.detail.lower()


@pytest.mark.asyncio(loop_scope="function")
async def test_pdf_rejected_before_docx_generation_completes():
    async with _test_session_factory() as s:
        user = await _user(s, "pdf2")
        export = await _export(s, user_id=user.id, format="docx", status="processing")
        await s.commit()
        export_id = export.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await export_pdf(exportId=export_id, request=_fake_request(), identity=identity, session=s)
        assert exc.value.status_code == 409
        assert "completed" in exc.value.detail.lower()


@pytest.mark.asyncio(loop_scope="function")
async def test_pdf_rejected_for_non_docx_source():
    async with _test_session_factory() as s:
        user = await _user(s, "pdf3")
        export = await _export(
            s, user_id=user.id, format="zip", status="completed",
            storage_key="exports/fake.zip", downloaded_at=datetime.now(timezone.utc),
        )
        await s.commit()
        export_id = export.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await export_pdf(exportId=export_id, request=_fake_request(), identity=identity, session=s)
        assert exc.value.status_code == 409
        assert "pdf" in exc.value.detail.lower()


@pytest.mark.asyncio(loop_scope="function")
async def test_pdf_accepted_after_docx_downloaded_and_creates_derived_export():
    async with _test_session_factory() as s:
        user = await _user(s, "pdf4")
        export = await _export(
            s, user_id=user.id, format="docx", status="completed",
            storage_key="exports/fake.docx", downloaded_at=datetime.now(timezone.utc),
        )
        await s.commit()
        export_id = export.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        result = await export_pdf(exportId=export_id, request=_fake_request(), identity=identity, session=s)
        assert result.status == "queued"
        assert result.format == "pdf"
        assert result.derived_from_export_id == export_id

    async with _test_session_factory() as verify_s:
        job_result = await verify_s.execute(
            select(ProcessingJob).where(
                ProcessingJob.job_type == "export_pdf",
            )
        )
        jobs = job_result.scalars().all()
        assert any(j.source_entity_id != export_id for j in jobs), "the pdf job must point at the NEW pdf export row, not the source docx"


# ═══════════════════════════════════════════════════════════════════════
# GET /exports/templates
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_list_templates_returns_registered_cv_templates():
    templates = await list_export_templates()
    ids = {t.id for t in templates}
    assert ids == {"standard", "compact"}
