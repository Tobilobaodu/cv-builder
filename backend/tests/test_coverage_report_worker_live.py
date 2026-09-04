"""Live-DB tests for process_coverage_report / _get_or_run_match /
_run_and_persist_match (Sprint 5 / Product Extension #2).

Mirrors test_ats_check_live.py's docling/magic-stub pattern exactly
(worker_jobs.py imports docling at module level, which isn't installed
on this host venv) — calls process_coverage_report(job_id) directly, no
Celery/Redis needed. Proves: reuse of an existing completed MatchRun
(no duplicate created), a fresh MatchRun created+persisted when none
exists, graceful skip of a job post with no JobPostProfile yet (report
still completes), and aggregate ranking against a small constructed
fixture with a known shared gap.

_run_and_persist_match now calls app.services.match_analysis.run_match_llm
(an LLM call) instead of the old rules-based match_engine.run_match() —
every test below monkeypatches run_match_llm to a small deterministic
fake so this file makes no real API calls, and seeds CvRawText (the LLM
engine reads the CV's raw text, not cv_profile_versions.structured_payload,
which is now just the FK-satisfying shim's minimal basics/skills payload).
"""
import sys
import types
import uuid

import pytest
from sqlalchemy import func, select
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
from app.workers.worker_jobs import _get_or_run_match, process_coverage_report
from app.db.models import (
    CoverageReport, CvFile, CvProfileVersion, CvRawText, CvSkillItem, JobPost,
    JobPostCollection, JobPostProfile, MatchEvidenceItem, MatchRun,
    ProcessingJob, User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


def _fake_run_match_llm(cv_text, job_post_text, job_post_profile=None, *, client=None):
    """Deterministic stand-in for app.services.match_analysis.run_match_llm:
    a required skill is "supported" if its name (case-insensitively)
    appears in cv_text, else "unsupported" — mirrors what the fixtures
    below actually need (a CV whose raw text mentions "Python" but not
    "Kubernetes") without making a real LLM call."""
    from app.extraction.match_engine import SUPPORTED, UNSUPPORTED, EvidenceItem
    from app.services.match_analysis import MatchAnalysisResult

    required = list((job_post_profile or {}).get("required_skills") or [])
    cv_lower = (cv_text or "").lower()
    items = [
        EvidenceItem(
            requirement_text=skill,
            requirement_type="required",
            support_level=SUPPORTED if skill.lower() in cv_lower else UNSUPPORTED,
            confidence=0.8,
        )
        for skill in required
    ]
    supported = sum(1 for e in items if e.support_level == SUPPORTED)
    unsupported = sum(1 for e in items if e.support_level == UNSUPPORTED)
    return MatchAnalysisResult(
        score=round(supported / max(len(items), 1), 2),
        supported_count=supported,
        partial_count=0,
        unsupported_count=unsupported,
        contradictory_count=0,
        unclear_count=0,
        total_requirements=len(items),
        summary_analysis="fake match analysis for coverage-report tests",
        evidence_items=items,
    )


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


async def _cv_profile_version(session, user, *, skills=()):
    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=user.id, filename="cv.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="parsed",
    )
    session.add(cv_file)
    await session.flush()
    # _run_and_persist_match now reads the CV's raw text (via
    # cv_profile_version.cv_file_id), not structured_payload — without
    # this row it raises before _fake_run_match_llm is ever reached.
    session.add(CvRawText(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id,
        canonical_text="Experienced engineer. Skills: " + ", ".join(skills or ["Python"]),
    ))
    pv = CvProfileVersion(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, user_id=user.id, version_number=1,
        profile_hash=uuid.uuid4().hex, schema_version="1.0",
        structured_payload={"basics": {}, "workExperience": [], "education": []},
    )
    session.add(pv)
    await session.flush()
    for skill in skills:
        session.add(CvSkillItem(id=str(uuid.uuid4()), cv_profile_version_id=pv.id, skill_name=skill))
    await session.flush()
    return pv


async def _job_post_with_profile(session, user, *, required_skills):
    jp = JobPost(
        id=str(uuid.uuid4()), user_id=user.id, source_type="text",
        raw_text="posting text" * 5, status="structured",
    )
    session.add(jp)
    await session.flush()
    jp_profile = JobPostProfile(
        id=str(uuid.uuid4()), job_post_id=jp.id, required_skills=list(required_skills),
    )
    session.add(jp_profile)
    await session.flush()
    return jp, jp_profile


async def _job_post_without_profile(session, user):
    jp = JobPost(
        id=str(uuid.uuid4()), user_id=user.id, source_type="text",
        raw_text="unparsed posting" * 5, status="pending",
    )
    session.add(jp)
    await session.flush()
    return jp


async def _collection(session, user, job_post_ids):
    c = JobPostCollection(id=str(uuid.uuid4()), user_id=user.id, name="Roles", job_post_ids=job_post_ids)
    session.add(c)
    await session.flush()
    return c


async def _report_and_job(session, user, collection, cv_profile_version_id):
    report = CoverageReport(
        id=str(uuid.uuid4()), user_id=user.id, cv_profile_version_id=cv_profile_version_id,
        collection_id=collection.id, match_run_ids=[], aggregate_gaps=[], status="pending",
    )
    session.add(report)
    await session.flush()
    job = ProcessingJob(
        id=str(uuid.uuid4()), job_type="coverage_report", source_entity_type="coverage_report",
        source_entity_id=report.id, user_id=user.id, status="pending",
    )
    session.add(job)
    await session.flush()
    return report, job


class TestProcessCoverageReport:
    def test_completes_with_gap_shared_across_two_posts(self, monkeypatch):
        import app.services.match_analysis as match_analysis
        monkeypatch.setattr(match_analysis, "run_match_llm", _fake_run_match_llm)

        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "run1")
                pv = await _cv_profile_version(s, user, skills=["Python"])
                jp1, _ = await _job_post_with_profile(s, user, required_skills=["Kubernetes"])
                jp2, _ = await _job_post_with_profile(s, user, required_skills=["Kubernetes"])
                collection = await _collection(s, user, [jp1.id, jp2.id])
                report, job = await _report_and_job(s, user, collection, pv.id)
                await s.commit()
                return report.id, job.id

        import asyncio
        report_id, job_id = asyncio.run(_seed())

        process_coverage_report(job_id)

        async def _verify():
            async with _test_session_factory() as s:
                report = await s.get(CoverageReport, report_id)
                job = await s.get(ProcessingJob, job_id)
                return report, job

        report, job = asyncio.run(_verify())
        assert job.status == "completed"
        assert report.status == "completed"
        assert len(report.match_run_ids) == 2
        assert report.skipped_job_post_ids in (None, [])
        gaps = {g["requirement_text_cluster"]: g for g in report.aggregate_gaps}
        assert "Kubernetes" in gaps
        assert gaps["Kubernetes"]["recurrence_count"] == 2
        assert gaps["Kubernetes"]["recurrence_ratio"] == 1.0

    def test_job_post_with_no_profile_is_skipped_not_blocking(self, monkeypatch):
        import app.services.match_analysis as match_analysis
        monkeypatch.setattr(match_analysis, "run_match_llm", _fake_run_match_llm)

        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "run2")
                pv = await _cv_profile_version(s, user, skills=["Python"])
                jp1, _ = await _job_post_with_profile(s, user, required_skills=["Kubernetes"])
                jp2 = await _job_post_without_profile(s, user)
                collection = await _collection(s, user, [jp1.id, jp2.id])
                report, job = await _report_and_job(s, user, collection, pv.id)
                await s.commit()
                return report.id, job.id, jp2.id

        import asyncio
        report_id, job_id, unparsed_jp_id = asyncio.run(_seed())

        process_coverage_report(job_id)

        async def _verify():
            async with _test_session_factory() as s:
                return await s.get(CoverageReport, report_id)

        report = asyncio.run(_verify())
        assert report.status == "completed"
        assert report.skipped_job_post_ids == [unparsed_jp_id]
        assert len(report.match_run_ids) == 1
        # total_posts stays 2 (full nominal collection size) even though
        # only 1 posting actually produced evidence — the recurrence
        # ratio must honestly reflect the skip, not silently shrink the
        # denominator to hide it.
        gaps = {g["requirement_text_cluster"]: g for g in report.aggregate_gaps}
        assert gaps["Kubernetes"]["recurrence_ratio"] == 0.5


class TestGetOrRunMatch:
    def test_reuses_existing_completed_match_run(self):
        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "reuse1")
                pv = await _cv_profile_version(s, user, skills=["Python"])
                jp, jp_profile = await _job_post_with_profile(s, user, required_skills=["Kubernetes"])
                existing = MatchRun(
                    id=str(uuid.uuid4()), user_id=user.id, cv_profile_version_id=pv.id,
                    job_post_profile_id=jp_profile.id, status="completed", score=0.5,
                )
                s.add(existing)
                await s.flush()
                s.add(MatchEvidenceItem(
                    id=str(uuid.uuid4()), match_run_id=existing.id, requirement_text="Kubernetes",
                    requirement_type="required", support_level="unsupported",
                ))
                await s.commit()

                count_before = (await s.execute(select(func.count()).select_from(MatchRun))).scalar_one()
                return existing.id, pv.id, jp_profile.id, user.id, count_before

        import asyncio
        existing_id, pv_id, jp_profile_id, user_id, count_before = asyncio.run(_seed())

        # _get_or_run_match takes a sync SQLAlchemy Session (worker_jobs.py's
        # own sync engine), not the async one used for seeding — mirror
        # worker_jobs.py::_get_sync_session exactly.
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        sync_engine = create_engine(settings.database_url)
        with Session(sync_engine) as sync_session:
            result = _get_or_run_match(
                sync_session, user_id=user_id, cv_profile_version_id=pv_id, job_post_profile_id=jp_profile_id,
            )
            assert result.id == existing_id, "must reuse the existing completed MatchRun, not create a new one"

            count_after = sync_session.execute(select(func.count()).select_from(MatchRun)).scalar_one()
        assert count_after == count_before, "no duplicate MatchRun should have been created"

    def test_runs_fresh_match_when_none_exists(self, monkeypatch):
        import app.services.match_analysis as match_analysis
        monkeypatch.setattr(match_analysis, "run_match_llm", _fake_run_match_llm)

        async def _seed():
            async with _test_session_factory() as s:
                user = await _user(s, "reuse2")
                pv = await _cv_profile_version(s, user, skills=["Python"])
                jp, jp_profile = await _job_post_with_profile(s, user, required_skills=["Kubernetes"])
                await s.commit()
                return user.id, pv.id, jp_profile.id

        import asyncio
        user_id, pv_id, jp_profile_id = asyncio.run(_seed())

        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        sync_engine = create_engine(settings.database_url)
        with Session(sync_engine) as sync_session:
            result = _get_or_run_match(
                sync_session, user_id=user_id, cv_profile_version_id=pv_id, job_post_profile_id=jp_profile_id,
            )
            sync_session.commit()
            assert result.status == "completed"
            assert result.total_requirements is not None and result.total_requirements > 0

            evidence = sync_session.execute(
                select(MatchEvidenceItem).where(MatchEvidenceItem.match_run_id == result.id)
            ).scalars().all()
            assert len(evidence) > 0
