"""Sprint 2: cleanup_expired_trial_sessions() — the highest-risk piece of
Sprint 2, since its FK-dependency delete ordering was reasoned out from
the model relationships but never actually run before this test.

app.workers.worker_jobs imports docling at module level for its (unrelated)
extraction tasks — a multi-GB ML dependency not needed to test this one
maintenance task. Stubbed out below so this file can import the real
module and call the real function without installing it.
"""
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

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

from app.core.config import settings
from app.workers.worker_jobs import cleanup_expired_trial_sessions
from app.db.models import (
    CvEducationItem,
    CvExperienceItem,
    CvExtractionPass,
    CvFile,
    CvProfile,
    CvProfileVersion,
    CvRawText,
    CvSkillItem,
    JobPost,
    JobPostProfile,
    MatchEvidenceItem,
    MatchRun,
    ProcessingJob,
    TrialSession,
    User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _trial_session(session, expires_delta, claimed_by_user_id=None):
    ts = TrialSession(id=str(uuid.uuid4()),
                       expires_at=datetime.now(timezone.utc) + expires_delta,
                       claimed_by_user_id=claimed_by_user_id)
    session.add(ts)
    await session.flush()
    return ts


async def _seed_full_row_tree(session, trial_session_id):
    """One row (or more) in every table cleanup is responsible for."""
    cv_file = CvFile(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                      filename="x.pdf", mime_type="application/pdf", file_size=1,
                      storage_key=str(uuid.uuid4()), status="parsed")
    session.add(cv_file)
    await session.flush()

    session.add_all([
        CvExtractionPass(id=str(uuid.uuid4()), cv_file_id=cv_file.id, pass_type="docling",
                          attempt_number=1, extracted_text="hello"),
        CvExtractionPass(id=str(uuid.uuid4()), cv_file_id=cv_file.id, pass_type="textract",
                          attempt_number=1, extracted_text="hello"),
    ])
    session.add(CvRawText(id=str(uuid.uuid4()), cv_file_id=cv_file.id, canonical_text="hello"))

    pv = CvProfileVersion(id=str(uuid.uuid4()), cv_file_id=cv_file.id,
                           trial_session_id=trial_session_id, version_number=1,
                           profile_hash=uuid.uuid4().hex, schema_version="1.0",
                           structured_payload={})
    session.add(pv)
    await session.flush()

    session.add_all([
        CvExperienceItem(id=str(uuid.uuid4()), cv_profile_version_id=pv.id, company="Acme"),
        CvEducationItem(id=str(uuid.uuid4()), cv_profile_version_id=pv.id, institution="U"),
        CvSkillItem(id=str(uuid.uuid4()), cv_profile_version_id=pv.id, skill_name="Python"),
    ])
    session.add(CvProfile(id=str(uuid.uuid4()), cv_file_id=cv_file.id, current_version_id=pv.id))

    jp = JobPost(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                 source_type="text", raw_text="x" * 150, status="completed")
    session.add(jp)
    await session.flush()
    jpp = JobPostProfile(id=str(uuid.uuid4()), job_post_id=jp.id)
    session.add(jpp)
    await session.flush()

    match_run = MatchRun(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                          cv_profile_version_id=pv.id, job_post_profile_id=jpp.id,
                          status="completed")
    session.add(match_run)
    await session.flush()
    session.add(MatchEvidenceItem(id=str(uuid.uuid4()), match_run_id=match_run.id,
                                   requirement_text="Python", requirement_type="required",
                                   support_level="supported"))

    session.add_all([
        ProcessingJob(id=str(uuid.uuid4()), trial_session_id=trial_session_id, job_type="match",
                      source_entity_type="match_run", source_entity_id=match_run.id,
                      status="completed"),
        ProcessingJob(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                      job_type="docling_extract", source_entity_type="cv_file",
                      source_entity_id=cv_file.id, status="failed"),
    ])

    await session.flush()
    return cv_file.id, pv.id, jp.id, match_run.id


@pytest.mark.asyncio(loop_scope="function")
async def test_cleanup_deletes_full_row_tree_for_expired_unclaimed_session():
    async with _test_session_factory() as s:
        ts = await _trial_session(s, expires_delta=timedelta(hours=-1))
        cv_file_id, pv_id, jp_id, match_run_id = await _seed_full_row_tree(s, ts.id)
        await s.commit()
        ts_id = ts.id

    cleanup_expired_trial_sessions()

    async with _test_session_factory() as verify_s:
        assert (await verify_s.execute(select(TrialSession).where(TrialSession.id == ts_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(CvFile).where(CvFile.id == cv_file_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(CvProfileVersion).where(CvProfileVersion.id == pv_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(CvProfile).where(CvProfile.cv_file_id == cv_file_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(CvExtractionPass).where(CvExtractionPass.cv_file_id == cv_file_id))).first() is None
        assert (await verify_s.execute(select(CvRawText).where(CvRawText.cv_file_id == cv_file_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(CvExperienceItem).where(CvExperienceItem.cv_profile_version_id == pv_id))).first() is None
        assert (await verify_s.execute(select(CvEducationItem).where(CvEducationItem.cv_profile_version_id == pv_id))).first() is None
        assert (await verify_s.execute(select(CvSkillItem).where(CvSkillItem.cv_profile_version_id == pv_id))).first() is None
        assert (await verify_s.execute(select(JobPost).where(JobPost.id == jp_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(JobPostProfile).where(JobPostProfile.job_post_id == jp_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(MatchRun).where(MatchRun.id == match_run_id))).scalar_one_or_none() is None
        assert (await verify_s.execute(select(MatchEvidenceItem).where(MatchEvidenceItem.match_run_id == match_run_id))).first() is None
        assert (await verify_s.execute(select(ProcessingJob).where(ProcessingJob.trial_session_id == ts_id))).first() is None


@pytest.mark.asyncio(loop_scope="function")
async def test_cleanup_ignores_claimed_sessions():
    async with _test_session_factory() as s:
        user = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}@test.example",
                    password_hash="fake", status="active")
        session_ = s
        session_.add(user)
        await session_.flush()
        ts = await _trial_session(s, expires_delta=timedelta(hours=-1), claimed_by_user_id=user.id)
        cv_file_id, *_ = await _seed_full_row_tree(s, ts.id)
        # Simulate claim-trial having already reassigned the CV file (a
        # claimed session's rows are never left pointing at trial_session_id).
        cv_file = (await s.execute(select(CvFile).where(CvFile.id == cv_file_id))).scalar_one()
        cv_file.user_id = user.id
        cv_file.trial_session_id = None
        await s.commit()
        ts_id = ts.id

    cleanup_expired_trial_sessions()

    async with _test_session_factory() as verify_s:
        assert (await verify_s.execute(select(TrialSession).where(TrialSession.id == ts_id))).scalar_one_or_none() is not None
        assert (await verify_s.execute(select(CvFile).where(CvFile.id == cv_file_id))).scalar_one_or_none() is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_cleanup_ignores_unexpired_sessions():
    async with _test_session_factory() as s:
        ts = await _trial_session(s, expires_delta=timedelta(hours=24))
        cv_file_id, *_ = await _seed_full_row_tree(s, ts.id)
        await s.commit()
        ts_id = ts.id

    cleanup_expired_trial_sessions()

    async with _test_session_factory() as verify_s:
        assert (await verify_s.execute(select(TrialSession).where(TrialSession.id == ts_id))).scalar_one_or_none() is not None
        assert (await verify_s.execute(select(CvFile).where(CvFile.id == cv_file_id))).scalar_one_or_none() is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_cleanup_handles_no_expired_sessions_without_error():
    # Should simply no-op, not raise, when there's nothing to clean up.
    cleanup_expired_trial_sessions()
