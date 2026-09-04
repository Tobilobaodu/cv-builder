"""Live-DB verification for Product Extension #1: ATS structural-readiness check.

Mirrors test_trial_session_cleanup.py's docling-stub pattern exactly since
worker_jobs.py imports docling at module level. Calls process_ats_check()
directly as a plain function — no Celery/Redis needed.
"""
import sys
import types
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

# ── Stub docling / docling_core (verbatim from test_trial_session_cleanup.py) ──
if "docling" not in sys.modules:
    _base_models = types.ModuleType("docling.datamodel.base_models")
    _base_models.InputFormat = object
    _pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    _pipeline_options.PdfPipelineOptions = object
    _document_converter = types.ModuleType("docling.document_converter")
    _document_converter.DocumentConverter = object
    _document_converter.PdfFormatOption = object
    _document_converter.WordFormatOption = object
    _docling_core_io = types.ModuleType("docling_core.types.io")
    _docling_core_io.DocumentStream = object

    sys.modules["docling"] = types.ModuleType("docling")
    sys.modules["docling.datamodel"] = types.ModuleType("docling.datamodel")
    sys.modules["docling.datamodel.base_models"] = _base_models
    sys.modules["docling.datamodel.pipeline_options"] = _pipeline_options
    sys.modules["docling.document_converter"] = _document_converter
    sys.modules["docling_core"] = types.ModuleType("docling_core")
    sys.modules["docling_core.types"] = types.ModuleType("docling_core.types")
    sys.modules["docling_core.types.io"] = _docling_core_io

# ── Stub magic (libmagic native lib not available) ────────────────────
if "magic" not in sys.modules:
    _magic = types.ModuleType("magic")
    _magic.MagicException = Exception
    _magic.from_buffer = lambda buf, mime=False: (
        "application/pdf" if b"PDF" in (buf or b"") else "application/octet-stream"
    )
    _magic.from_file = lambda path, mime=False: "application/octet-stream"
    sys.modules["magic"] = _magic

from app.core.config import settings
from app.workers.worker_jobs import process_ats_check
from app.api.v1.cvs import run_ats_check_for_cv, get_ats_check
from app.db.models import (
    AtsReadinessCheck, CvFile, CvExtractionPass, CvProfile, CvProfileVersion,
    CvRawText, ProcessingJob, User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _user(session, tag=""):
    u = User(id=str(uuid.uuid4()),
             email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
             password_hash="fake", status="active")
    session.add(u)
    await session.flush()
    return u


async def _seed_full_chain(session, user, *, include_profile=True):
    """Seed User, CvFile, CvRawText, two CvExtractionPasses, and optionally
    CvProfile + CvProfileVersion."""
    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=user.id,
        filename="test.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="parsed",
    )
    session.add(cv_file)
    await session.flush()

    raw_text = CvRawText(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id,
        canonical_text=(
            "jane.doe@example.com  +1 555 0100\n"
            "WORK EXPERIENCE\nSoftware Engineer at Acme Corp\n"
            " - Built APIs in Python and Docker\n"
            "EDUCATION\nBSc Computer Science\n"
            "SKILLS\nPython, SQL, Docker\n"
        ),
        ocr_used=False,
        structural_validation_result={
            "sectionCountMatch": True, "headingAlignmentScore": 0.9,
            "readingOrderConsistent": True, "dateRangeConsistent": True,
            "bulletPreservationScore": 0.8, "anomalyDetected": False,
        },
    )
    session.add(raw_text)

    session.add(CvExtractionPass(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, pass_type="docling",
        attempt_number=1, extracted_text="experienced python developer",
    ))
    session.add(CvExtractionPass(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, pass_type="textract",
        attempt_number=1,
        extracted_text="experienced python developer docker kubernetes aws",
    ))
    await session.flush()

    pv_id = None
    if include_profile:
        pv = CvProfileVersion(
            id=str(uuid.uuid4()), cv_file_id=cv_file.id,
            user_id=user.id, version_number=1,
            profile_hash=uuid.uuid4().hex, schema_version="1.0",
            structured_payload={
                "basics": {
                    "name": "Jane Doe",
                    "email": "jane.doe@example.com",
                    "phone": "+1 555 0100",
                },
                "heading_names": ["Work Experience", "Education", "Skills"],
            },
        )
        session.add(pv)
        await session.flush()
        profile = CvProfile(
            id=str(uuid.uuid4()), cv_file_id=cv_file.id,
            current_version_id=pv.id,
        )
        session.add(profile)
        await session.flush()
        pv_id = pv.id

    return cv_file, pv_id


def _job(session, cv_file, status="queued"):
    j = ProcessingJob(
        id=str(uuid.uuid4()), job_type="ats_check",
        source_entity_type="cv_file", source_entity_id=cv_file.id,
        user_id=cv_file.user_id, status=status,
    )
    session.add(j)
    return j


# ═══════════════════════════════════════════════════════════════════════
# Case 1 — Full chain: seed → process_ats_check → verify
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_full_chain_completes_and_persists_result():
    async with _test_session_factory() as s:
        user = await _user(s, "fullchain")
        cv_file, pv_id = await _seed_full_chain(s, user, include_profile=True)
        job = _job(s, cv_file, status="queued")
        await s.commit()
        job_id = job.id
        cv_file_id = cv_file.id

    process_ats_check(job_id)

    async with _test_session_factory() as verify_s:
        job_row = await verify_s.get(ProcessingJob, job_id)
        assert job_row.status == "completed"

        check_row = (await verify_s.execute(
            select(AtsReadinessCheck)
            .where(AtsReadinessCheck.cv_file_id == cv_file_id)
        )).scalar_one()
        assert check_row.cv_file_id == cv_file_id
        assert check_row.cv_profile_version_id == pv_id
        assert 0.0 <= check_row.overall_score <= 1.0
        assert len(check_row.checks) == 6
        check_types = {c["check_type"] for c in check_row.checks}
        expected = {"text_in_image", "layout_structure", "contact_info_location",
                    "non_standard_characters", "section_heading_recognizability",
                    "file_format_signals"}
        assert check_types == expected


# ═══════════════════════════════════════════════════════════════════════
# Case 2 — No profile yet (cv_profile_version_id IS NULL)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_no_profile_still_completes_with_null_pv_id():
    async with _test_session_factory() as s:
        user = await _user(s, "noprofile")
        cv_file, _ = await _seed_full_chain(s, user, include_profile=False)
        job = _job(s, cv_file, status="queued")
        await s.commit()
        job_id = job.id
        cv_file_id = cv_file.id

    process_ats_check(job_id)

    async with _test_session_factory() as verify_s:
        job_row = await verify_s.get(ProcessingJob, job_id)
        assert job_row.status == "completed"

        check_row = (await verify_s.execute(
            select(AtsReadinessCheck)
            .where(AtsReadinessCheck.cv_file_id == cv_file_id)
        )).scalar_one()
        assert check_row.cv_profile_version_id is None, (
            "cv_profile_version_id must be nullable — the table definition "
            "requires it, and process_ats_check must not crash when no "
            "profile exists yet."
        )


# ═══════════════════════════════════════════════════════════════════════
# Case 3 — GET response shape (cv_id field is present)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_get_response_has_cv_id():
    async with _test_session_factory() as s:
        user = await _user(s, "getshape")
        cv_file, _ = await _seed_full_chain(s, user, include_profile=True)
        job = _job(s, cv_file, status="queued")
        await s.commit()
        job_id = job.id

    process_ats_check(job_id)

    async with _test_session_factory() as verify_s:
        response = await get_ats_check(
            cv_id=cv_file.id, current_user=user, session=verify_s,
        )
        assert response.cv_id == cv_file.id, (
            "GET response must include cvId — this was the exact field "
            "that would crash before the fix."
        )


# ═══════════════════════════════════════════════════════════════════════
# Case 4 — Ownership check: wrong user gets 404
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_post_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "owner")
        cv_file, _ = await _seed_full_chain(s, owner, include_profile=True)
        await s.commit()

    async with _test_session_factory() as s:
        attacker = await _user(s, "attacker")
        await s.commit()

    from types import SimpleNamespace
    fake_req = SimpleNamespace()
    fake_req.client = SimpleNamespace(host="127.0.0.1")
    fake_req.headers = {}

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await run_ats_check_for_cv(
                request=fake_req, cv_id=cv_file.id,
                current_user=attacker, session=s,
            )
        assert exc.value.status_code == 404
