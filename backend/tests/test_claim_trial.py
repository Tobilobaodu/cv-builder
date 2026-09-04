"""Sprint 2: claim_trial_session()'s reassignment transaction.

Seeds a trial session with rows across all 5 trial-eligible tables
(including cv_profile_versions — the one missing from the original
design, easiest to regress) and verifies the reassignment, the 404/409
rejection paths, and — since claim_trial_session() itself never commits —
that not committing means nothing persists, which is the actual
atomicity guarantee the single-commit design provides.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.services.trial_session import claim_trial_session
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


async def _trial_session(session, expires_delta=timedelta(hours=24), claimed_by_user_id=None):
    ts = TrialSession(id=str(uuid.uuid4()),
                       expires_at=datetime.now(timezone.utc) + expires_delta,
                       claimed_by_user_id=claimed_by_user_id)
    session.add(ts)
    await session.flush()
    return ts


async def _seed_full_trial_tree(session, trial_session_id):
    """One row in each of the 5 trial-eligible tables, all pointing at
    the same trial session."""
    cv_file = CvFile(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                      filename="x.pdf", mime_type="application/pdf", file_size=1,
                      storage_key=str(uuid.uuid4()), status="parsed")
    session.add(cv_file)
    await session.flush()

    pv = CvProfileVersion(id=str(uuid.uuid4()), cv_file_id=cv_file.id,
                           trial_session_id=trial_session_id, version_number=1,
                           profile_hash=uuid.uuid4().hex, schema_version="1.0",
                           structured_payload={})
    session.add(pv)

    jp = JobPost(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                 source_type="text", raw_text="x" * 150, status="completed")
    session.add(jp)
    await session.flush()

    jpp = JobPostProfile(id=str(uuid.uuid4()), job_post_id=jp.id)
    session.add(jpp)
    await session.flush()

    match_run = MatchRun(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                          cv_profile_version_id=pv.id, job_post_profile_id=jpp.id,
                          status="pending")
    session.add(match_run)

    proc_job = ProcessingJob(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                              job_type="match", source_entity_type="match_run",
                              source_entity_id=str(uuid.uuid4()), status="completed")
    session.add(proc_job)

    await session.flush()
    return cv_file, pv, jp, match_run, proc_job


@pytest.mark.asyncio(loop_scope="function")
async def test_claim_reassigns_all_five_tables_and_marks_claimed():
    async with _test_session_factory() as s:
        ts = await _trial_session(s)
        user = await _user(s, "claim1")
        cv_file, pv, jp, match_run, proc_job = await _seed_full_trial_tree(s, ts.id)
        await s.commit()

        result = await claim_trial_session(s, ts.id, user.id)
        await s.commit()

        assert result.cv_files_reassigned == 1
        assert result.job_posts_reassigned == 1
        assert result.match_runs_reassigned == 1

    async with _test_session_factory() as verify_s:
        for model, row_id in [
            (CvFile, cv_file.id), (CvProfileVersion, pv.id),
            (JobPost, jp.id), (MatchRun, match_run.id), (ProcessingJob, proc_job.id),
        ]:
            row = (await verify_s.execute(select(model).where(model.id == row_id))).scalar_one()
            assert row.user_id == user.id, f"{model.__name__} not reassigned"
            assert row.trial_session_id is None, f"{model.__name__} still has trial_session_id"

        refreshed_ts = (await verify_s.execute(
            select(TrialSession).where(TrialSession.id == ts.id)
        )).scalar_one()
        assert refreshed_ts.claimed_by_user_id == user.id
        assert refreshed_ts.claimed_at is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_claim_not_committed_leaves_nothing_persisted():
    """claim_trial_session() itself never commits — proving that not
    committing leaves the DB state untouched is the actual guarantee the
    single-commit design provides (this is what a caller failing after
    the reassignment but before its own commit would look like)."""
    async with _test_session_factory() as s:
        ts = await _trial_session(s)
        user = await _user(s, "claim2")
        cv_file, pv, jp, match_run, proc_job = await _seed_full_trial_tree(s, ts.id)
        await s.commit()
        ts_id, cv_file_id = ts.id, cv_file.id  # capture before rollback expires attributes

        await claim_trial_session(s, ts_id, user.id)
        await s.rollback()  # simulates a failure after reassignment, before commit

    async with _test_session_factory() as verify_s:
        row = (await verify_s.execute(select(CvFile).where(CvFile.id == cv_file_id))).scalar_one()
        assert row.trial_session_id == ts_id, "reassignment must not have persisted"
        assert row.user_id is None

        refreshed_ts = (await verify_s.execute(
            select(TrialSession).where(TrialSession.id == ts_id)
        )).scalar_one()
        assert refreshed_ts.claimed_by_user_id is None, "claim must not have persisted"


@pytest.mark.asyncio(loop_scope="function")
async def test_claiming_already_claimed_session_returns_409_and_does_not_reassign():
    async with _test_session_factory() as s:
        first_user = await _user(s, "first")
        second_user = await _user(s, "second")
        ts = await _trial_session(s)
        cv_file, *_ = await _seed_full_trial_tree(s, ts.id)
        await s.commit()

        await claim_trial_session(s, ts.id, first_user.id)
        await s.commit()

        with pytest.raises(HTTPException) as exc:
            await claim_trial_session(s, ts.id, second_user.id)
        assert exc.value.status_code == 409

    async with _test_session_factory() as verify_s:
        row = (await verify_s.execute(select(CvFile).where(CvFile.id == cv_file.id))).scalar_one()
        assert row.user_id == first_user.id, "second claim must not have re-reassigned"


@pytest.mark.asyncio(loop_scope="function")
async def test_claiming_expired_session_returns_409():
    async with _test_session_factory() as s:
        ts = await _trial_session(s, expires_delta=timedelta(hours=-1))
        user = await _user(s, "expired")
        await s.commit()

        with pytest.raises(HTTPException) as exc:
            await claim_trial_session(s, ts.id, user.id)
        assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="function")
async def test_claiming_nonexistent_session_returns_404():
    async with _test_session_factory() as s:
        user = await _user(s, "nonexistent")
        await s.commit()

        with pytest.raises(HTTPException) as exc:
            await claim_trial_session(s, str(uuid.uuid4()), user.id)
        assert exc.value.status_code == 404
