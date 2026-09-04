"""Live-DB integration tests for the new LLM-based CV analysis / match
pipeline: process_cv_analyze, the FK-satisfying CvProfileVersion/CvProfile
shim it writes, _run_and_persist_match's LLM-based rewrite, and (since the
shim is what unblocks it) cover_letters.py::start_workflow.

Mirrors test_ats_check_live.py's docling/magic-stub pattern exactly
(worker_jobs.py imports docling at module level, not installed on this
host venv) and test_cover_letter_generation.py's FakeCompletions/FakeClient
pattern for mocking the LLM. Calls process_cv_analyze(job_id)/
process_match(job_id) directly — no Celery/Redis needed. No real API calls
anywhere in this file.
"""
import sys
import types
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

# ── Stub docling / docling_core (verbatim from test_ats_check_live.py) ──
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

if "magic" not in sys.modules:
    _magic = types.ModuleType("magic")
    _magic.MagicException = Exception
    _magic.from_buffer = lambda buf, mime=False: "application/octet-stream"
    _magic.from_file = lambda path, mime=False: "application/octet-stream"
    sys.modules["magic"] = _magic

from app.core.config import settings
from app.workers.worker_jobs import process_cv_analyze, process_match
from app.api.v1.cover_letters import start_workflow
from app.schemas.cover_letter import StartWorkflowRequest
from app.db.models import (
    CvAnalysis, CvFile, CvProfile, CvProfileVersion, CvRawText, CvSkillItem,
    JobPost, JobPostProfile, MatchEvidenceItem, MatchRun, ProcessingJob, User,
)
from app.services.cv_analysis import CvAnalysisResult
from app.services.match_analysis import MatchAnalysisResult
from app.extraction.match_engine import SUPPORTED, UNSUPPORTED, EvidenceItem

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


async def _cv_with_raw_text(session, user, text="Jane Doe. Python engineer with 5 years experience."):
    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=user.id, filename="cv.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="completed",
    )
    session.add(cv_file)
    await session.flush()
    session.add(CvRawText(id=str(uuid.uuid4()), cv_file_id=cv_file.id, canonical_text=text))
    await session.flush()
    return cv_file


def _cv_analyze_job(session, cv_file):
    j = ProcessingJob(
        id=str(uuid.uuid4()), job_type="cv_analyze",
        source_entity_type="cv_file", source_entity_id=cv_file.id,
        user_id=cv_file.user_id, status="queued",
    )
    session.add(j)
    return j


_FAKE_CV_ANALYSIS = CvAnalysisResult(
    overall_score=68.0,
    skillset_score=60.0,
    formatting_score=75.0,
    ats_issues=[{"passed": False, "severity": "medium", "title": "Section headings", "detail": "Missing."}],
    formatting_issues=[{"passed": True, "severity": "low", "title": "Bullets", "detail": "Fine."}],
    tips=["Add a summary section."],
    basics={"name": "Jane Doe", "email": "jane@example.com", "phone": None},
    skills=["Python", "SQL"],
    prompt_tokens=50,
    completion_tokens=100,
)


def _fake_analyze_cv(cv_text, *, client=None):
    return _FAKE_CV_ANALYSIS


# ═══════════════════════════════════════════════════════════════════════
# process_cv_analyze
# ═══════════════════════════════════════════════════════════════════════


class TestProcessCvAnalyze:
    def test_persists_cv_analysis_and_writes_the_profile_shim(self, monkeypatch):
        import app.services.cv_analysis as cv_analysis_module
        monkeypatch.setattr(cv_analysis_module, "analyze_cv", _fake_analyze_cv)

        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "analyze1")
                cv_file = await _cv_with_raw_text(s, user)
                job = _cv_analyze_job(s, cv_file)
                await s.commit()
                return cv_file.id, job.id, user.id

        import asyncio
        cv_file_id, job_id, user_id = asyncio.run(_seed())

        process_cv_analyze(job_id)

        async def _verify():
            async with _test_session_factory() as s:
                job = await s.get(ProcessingJob, job_id)
                analysis = (await s.execute(
                    select(CvAnalysis).where(CvAnalysis.cv_file_id == cv_file_id)
                )).scalar_one()
                profile = (await s.execute(
                    select(CvProfile).where(CvProfile.cv_file_id == cv_file_id)
                )).scalar_one()
                version = await s.get(CvProfileVersion, profile.current_version_id) \
                    if profile.current_version_id else None
                skills = (await s.execute(
                    select(CvSkillItem).where(
                        CvSkillItem.cv_profile_version_id == profile.current_version_id
                    )
                )).scalars().all() if profile.current_version_id else []
                return job, analysis, profile, version, skills

        job, analysis, profile, version, skills = asyncio.run(_verify())

        assert job.status == "completed"
        assert analysis.overall_score == 68.0
        assert analysis.skillset_score == 60.0
        assert analysis.formatting_score == 75.0
        assert analysis.tips == ["Add a summary section."]
        assert analysis.cv_profile_version_id is not None

        # The whole point of the shim: CvProfile.current_version_id must
        # now resolve to a real CvProfileVersion row.
        assert profile.current_version_id is not None
        assert version is not None
        assert version.structured_payload["basics"]["name"] == "Jane Doe"
        assert version.structured_payload["skills"] == ["Python", "SQL"]
        assert version.schema_version == "llm_shim_v1"
        assert {s.skill_name for s in skills} == {"Python", "SQL"}

    def test_missing_raw_text_fails_the_job(self):
        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "analyze2")
                cv_file = CvFile(
                    id=str(uuid.uuid4()), user_id=user.id, filename="cv.pdf",
                    mime_type="application/pdf", file_size=1,
                    storage_key=str(uuid.uuid4()), status="extracting",
                )
                s.add(cv_file)
                await s.flush()
                job = _cv_analyze_job(s, cv_file)
                await s.commit()
                return job.id

        import asyncio
        job_id = asyncio.run(_seed())

        with pytest.raises(Exception):
            process_cv_analyze(job_id)

        async def _verify():
            async with _test_session_factory() as s:
                return await s.get(ProcessingJob, job_id)

        job = asyncio.run(_verify())
        assert job.status == "failed"


# ═══════════════════════════════════════════════════════════════════════
# process_match
# ═══════════════════════════════════════════════════════════════════════


def _fake_run_match_llm(cv_text, job_post_text, job_post_profile=None, *, client=None):
    items = [
        EvidenceItem(
            requirement_text="Python", requirement_type="required",
            support_level=SUPPORTED, confidence=0.9,
            source_references=["cv text"],
        ),
        EvidenceItem(
            requirement_text="Kubernetes", requirement_type="required",
            support_level=UNSUPPORTED, confidence=0.0,
            warning="No evidence found.",
        ),
    ]
    return MatchAnalysisResult(
        score=0.5, supported_count=1, partial_count=0, unsupported_count=1,
        contradictory_count=0, unclear_count=0, total_requirements=2,
        summary_analysis="Strong on Python, missing Kubernetes.",
        evidence_items=items,
        ats_issues=[{"passed": True, "severity": "low", "title": "Contact", "detail": "OK"}],
        formatting_issues=[],
        tips=["Highlight container orchestration experience if any."],
        prompt_tokens=80, completion_tokens=120,
    )


class TestProcessMatch:
    def test_completes_and_persists_match_json_and_evidence(self, monkeypatch):
        import app.services.match_analysis as match_analysis_module
        monkeypatch.setattr(match_analysis_module, "run_match_llm", _fake_run_match_llm)

        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "match1")
                cv_file = await _cv_with_raw_text(s, user)
                pv = CvProfileVersion(
                    id=str(uuid.uuid4()), cv_file_id=cv_file.id, user_id=user.id,
                    version_number=1, profile_hash=uuid.uuid4().hex,
                    schema_version="llm_shim_v1",
                    structured_payload={"basics": {}, "skills": ["Python"]},
                )
                s.add(pv)
                await s.flush()

                jp = JobPost(
                    id=str(uuid.uuid4()), user_id=user.id, source_type="text",
                    raw_text="We need a Python and Kubernetes engineer." * 3,
                    status="structured",
                )
                s.add(jp)
                await s.flush()
                jp_profile = JobPostProfile(
                    id=str(uuid.uuid4()), job_post_id=jp.id,
                    job_title="Backend Engineer", employer="Acme",
                    required_skills=["Python", "Kubernetes"], preferred_skills=[],
                )
                s.add(jp_profile)
                await s.flush()

                match_run = MatchRun(
                    id=str(uuid.uuid4()), user_id=user.id, cv_profile_version_id=pv.id,
                    job_post_profile_id=jp_profile.id, status="pending",
                )
                s.add(match_run)
                await s.flush()

                job = ProcessingJob(
                    id=str(uuid.uuid4()), job_type="match",
                    source_entity_type="match_run", source_entity_id=match_run.id,
                    user_id=user.id, status="queued",
                )
                s.add(job)
                await s.commit()
                return job.id, match_run.id

        import asyncio
        job_id, match_run_id = asyncio.run(_seed())

        process_match(job_id)

        async def _verify():
            async with _test_session_factory() as s:
                job = await s.get(ProcessingJob, job_id)
                match_run = await s.get(MatchRun, match_run_id)
                evidence = (await s.execute(
                    select(MatchEvidenceItem).where(MatchEvidenceItem.match_run_id == match_run_id)
                )).scalars().all()
                return job, match_run, evidence

        job, match_run, evidence = asyncio.run(_verify())

        assert job.status == "completed"
        assert match_run.status == "completed"
        assert match_run.supported_count == 1
        assert match_run.unsupported_count == 1
        assert match_run.total_requirements == 2
        assert match_run.match_json is not None
        assert match_run.match_json["tips"] == ["Highlight container orchestration experience if any."]
        assert match_run.match_json["ats_issues"][0]["title"] == "Contact"
        assert len(evidence) == 2
        by_text = {e.requirement_text: e for e in evidence}
        assert by_text["Python"].support_level == "supported"
        assert by_text["Kubernetes"].support_level == "unsupported"


# ═══════════════════════════════════════════════════════════════════════
# cover_letters.py::start_workflow now works once cv_analyze has run
# ═══════════════════════════════════════════════════════════════════════


class TestStartWorkflowAfterCvAnalyze:
    def test_start_workflow_succeeds_once_cv_analyze_has_run(self, monkeypatch):
        import app.services.cv_analysis as cv_analysis_module
        monkeypatch.setattr(cv_analysis_module, "analyze_cv", _fake_analyze_cv)

        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "startwf1")
                cv_file = await _cv_with_raw_text(s, user)
                cv_analyze_job = _cv_analyze_job(s, cv_file)

                jp = JobPost(
                    id=str(uuid.uuid4()), user_id=user.id, source_type="text",
                    raw_text="We need a Python engineer." * 5, status="structured",
                )
                s.add(jp)
                await s.flush()
                jp_profile = JobPostProfile(
                    id=str(uuid.uuid4()), job_post_id=jp.id,
                    job_title="Backend Engineer", employer="Acme",
                    required_skills=["Python"], preferred_skills=[],
                )
                s.add(jp_profile)
                await s.commit()
                return user.id, cv_file.id, cv_analyze_job.id, jp.id

        import asyncio
        user_id, cv_file_id, cv_analyze_job_id, job_post_id = asyncio.run(_seed())

        # Before cv_analyze has run: CvProfile.current_version_id is still
        # null, so start_workflow must 404 — confirms the precondition
        # this whole chain fixes, rather than assuming it.
        fake_req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})

        async def _get_user(uid):
            async with _test_session_factory() as s:
                return await s.get(User, uid)

        user = asyncio.run(_get_user(user_id))

        async def _try_start_before():
            async with _test_session_factory() as s:
                with pytest.raises(Exception):
                    await start_workflow(
                        request=fake_req,
                        body=StartWorkflowRequest(cvId=cv_file_id, jobPostId=job_post_id),
                        current_user=user, session=s,
                    )

        asyncio.run(_try_start_before())

        process_cv_analyze(cv_analyze_job_id)

        async def _try_start_after():
            async with _test_session_factory() as s:
                result = await start_workflow(
                    request=fake_req,
                    body=StartWorkflowRequest(cvId=cv_file_id, jobPostId=job_post_id),
                    current_user=user, session=s,
                )
                return result

        result = asyncio.run(_try_start_after())
        assert result.status == "awaiting_answers"
        assert result.current_step == 1

        async def _verify_total_steps():
            async with _test_session_factory() as s:
                from app.db.models import CoverLetterWorkflow
                wf = (await s.execute(
                    select(CoverLetterWorkflow).where(CoverLetterWorkflow.id == result.id)
                )).scalar_one()
                return wf.total_steps

        total_steps = asyncio.run(_verify_total_steps())
        assert total_steps == 4
