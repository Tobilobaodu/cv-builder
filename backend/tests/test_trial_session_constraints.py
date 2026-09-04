"""Sprint 2: DB-level verification that the "exactly one owner" CHECK
constraints migration 006 added actually fire in Postgres — not just
that the columns exist. Requires a live database.

Mirrors test_job_concurrency_limit.py's style: own engine/session
factory off settings.database_url_async, NullPool, no TestClient, no
conftest.py — each test file is self-contained.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.models import (
    CvFile,
    CvProfileVersion,
    JobPost,
    JobPostProfile,
    MatchRun,
    ProcessingJob,
    TrialSession,
    User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _user(session, tag=""):
    u = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
             password_hash="fake", status="active")
    session.add(u)
    await session.flush()
    return u


async def _trial_session(session):
    ts = TrialSession(id=str(uuid.uuid4()),
                       expires_at=datetime.now(timezone.utc) + timedelta(hours=24))
    session.add(ts)
    await session.flush()
    return ts


async def _cv_profile_version(session, user_id=None, trial_session_id=None):
    cv_file = CvFile(id=str(uuid.uuid4()), user_id=user_id, trial_session_id=trial_session_id,
                      filename="test.pdf", mime_type="application/pdf", file_size=100,
                      storage_key=str(uuid.uuid4()), status="parsed")
    session.add(cv_file)
    await session.flush()
    pv = CvProfileVersion(id=str(uuid.uuid4()), cv_file_id=cv_file.id,
                           user_id=user_id, trial_session_id=trial_session_id,
                           version_number=1, profile_hash=uuid.uuid4().hex,
                           schema_version="1.0", structured_payload={})
    session.add(pv)
    await session.flush()
    return cv_file, pv


async def _job_post_profile(session, user_id=None, trial_session_id=None):
    jp = JobPost(id=str(uuid.uuid4()), user_id=user_id, trial_session_id=trial_session_id,
                 source_type="text", raw_text="x" * 150, status="completed")
    session.add(jp)
    await session.flush()
    jpp = JobPostProfile(id=str(uuid.uuid4()), job_post_id=jp.id)
    session.add(jpp)
    await session.flush()
    return jp, jpp


class TestCvFilesConstraint:

    @pytest.mark.asyncio(loop_scope="function")
    async def test_both_owners_set_raises(self):
        async with _test_session_factory() as s:
            u = await _user(s, "a")
            ts = await _trial_session(s)
            s.add(CvFile(id=str(uuid.uuid4()), user_id=u.id, trial_session_id=ts.id,
                          filename="x.pdf", mime_type="application/pdf", file_size=1,
                          storage_key=str(uuid.uuid4()), status="pending"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_neither_owner_set_raises(self):
        async with _test_session_factory() as s:
            s.add(CvFile(id=str(uuid.uuid4()), filename="x.pdf", mime_type="application/pdf",
                          file_size=1, storage_key=str(uuid.uuid4()), status="pending"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_user_only_succeeds(self):
        async with _test_session_factory() as s:
            u = await _user(s, "b")
            s.add(CvFile(id=str(uuid.uuid4()), user_id=u.id, filename="x.pdf",
                          mime_type="application/pdf", file_size=1,
                          storage_key=str(uuid.uuid4()), status="pending"))
            await s.flush()
            await s.commit()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_trial_session_only_succeeds(self):
        async with _test_session_factory() as s:
            ts = await _trial_session(s)
            s.add(CvFile(id=str(uuid.uuid4()), trial_session_id=ts.id, filename="x.pdf",
                          mime_type="application/pdf", file_size=1,
                          storage_key=str(uuid.uuid4()), status="pending"))
            await s.flush()
            await s.commit()


class TestMatchRunsConstraint:
    """match_runs also has two other required FKs (cv_profile_version_id,
    job_post_profile_id) — exercising the CHECK constraint here confirms
    it doesn't interact badly with those."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_both_owners_set_raises(self):
        async with _test_session_factory() as s:
            u = await _user(s, "c")
            ts = await _trial_session(s)
            _, pv = await _cv_profile_version(s, user_id=u.id)
            _, jpp = await _job_post_profile(s, user_id=u.id)
            s.add(MatchRun(id=str(uuid.uuid4()), user_id=u.id, trial_session_id=ts.id,
                            cv_profile_version_id=pv.id, job_post_profile_id=jpp.id,
                            status="pending"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_neither_owner_set_raises(self):
        async with _test_session_factory() as s:
            u = await _user(s, "d")
            _, pv = await _cv_profile_version(s, user_id=u.id)
            _, jpp = await _job_post_profile(s, user_id=u.id)
            s.add(MatchRun(id=str(uuid.uuid4()),
                            cv_profile_version_id=pv.id, job_post_profile_id=jpp.id,
                            status="pending"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_trial_session_only_succeeds(self):
        async with _test_session_factory() as s:
            ts = await _trial_session(s)
            _, pv = await _cv_profile_version(s, trial_session_id=ts.id)
            _, jpp = await _job_post_profile(s, trial_session_id=ts.id)
            s.add(MatchRun(id=str(uuid.uuid4()), trial_session_id=ts.id,
                            cv_profile_version_id=pv.id, job_post_profile_id=jpp.id,
                            status="pending"))
            await s.flush()
            await s.commit()


class TestProcessingJobsConstraint:
    """processing_jobs.user_id was already nullable before Sprint 2 — the
    highest-risk table for "only the column landed, not the constraint."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_both_owners_set_raises(self):
        async with _test_session_factory() as s:
            u = await _user(s, "e")
            ts = await _trial_session(s)
            s.add(ProcessingJob(id=str(uuid.uuid4()), user_id=u.id, trial_session_id=ts.id,
                                 job_type="match", source_entity_type="match_run",
                                 source_entity_id=str(uuid.uuid4()), status="pending"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_neither_owner_set_raises(self):
        async with _test_session_factory() as s:
            s.add(ProcessingJob(id=str(uuid.uuid4()), job_type="match",
                                 source_entity_type="match_run",
                                 source_entity_id=str(uuid.uuid4()), status="pending"))
            with pytest.raises(IntegrityError):
                await s.flush()
            await s.rollback()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_trial_session_only_succeeds(self):
        async with _test_session_factory() as s:
            ts = await _trial_session(s)
            s.add(ProcessingJob(id=str(uuid.uuid4()), trial_session_id=ts.id,
                                 job_type="match", source_entity_type="match_run",
                                 source_entity_id=str(uuid.uuid4()), status="pending"))
            await s.flush()
            await s.commit()
