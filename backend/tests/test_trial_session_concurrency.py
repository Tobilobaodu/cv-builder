"""Sprint 2: enforce_concurrent_job_limit() keyed by trial_session_id.

Mirrors tests/test_job_concurrency_limit.py's five scenarios exactly,
substituting a TrialSession for the User, plus the new XOR-guard cases
for the extended (user_id, trial_session_id) signature.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func as sql_func, select as sql_select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.services.orchestration import enforce_concurrent_job_limit
from app.db.models import ProcessingJob, TrialSession

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _trial_session(session):
    ts = TrialSession(id=str(uuid.uuid4()),
                       expires_at=datetime.now(timezone.utc) + timedelta(hours=24))
    session.add(ts)
    await session.flush()
    return ts


async def _job(session, trial_session_id, status="pending"):
    j = ProcessingJob(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                       job_type="match", source_entity_type="match_run",
                       source_entity_id=str(uuid.uuid4()), status=status)
    session.add(j)
    await session.flush()
    return j


async def _gated_submit(trial_session_id, job_status="pending"):
    async with _test_session_factory() as s:
        await enforce_concurrent_job_limit(s, trial_session_id=trial_session_id)
        j = ProcessingJob(id=str(uuid.uuid4()), trial_session_id=trial_session_id,
                           job_type="match", source_entity_type="match_run",
                           source_entity_id=str(uuid.uuid4()), status=job_status)
        s.add(j)
        await s.flush()
        await s.commit()
        return "ok"


@pytest.mark.asyncio(loop_scope="function")
async def test_all_scenarios():
    L = settings.max_concurrent_jobs_per_user

    # ── 1. Below-limit allowed ──────────────────────────────────────
    async with _test_session_factory() as s1:
        ts1 = await _trial_session(s1)
        for _ in range(L - 1):
            await _job(s1, ts1.id, "pending")
        await enforce_concurrent_job_limit(s1, trial_session_id=ts1.id)
        await s1.commit()

    # ── 2. At-limit → 429 ───────────────────────────────────────────
    got_429 = False
    async with _test_session_factory() as s2:
        ts2 = await _trial_session(s2)
        for _ in range(L):
            await _job(s2, ts2.id, "pending")
        try:
            await enforce_concurrent_job_limit(s2, trial_session_id=ts2.id)
            await s2.commit()
            pytest.fail("expected 429")
        except HTTPException as e:
            got_429 = True
            assert e.status_code == 429
            assert "Too many active" in e.detail
    assert got_429

    # ── 3. Other trial sessions not counted ─────────────────────────
    async with _test_session_factory() as s3:
        ts_a = await _trial_session(s3)
        ts_b = await _trial_session(s3)
        for _ in range(L):
            await _job(s3, ts_b.id, "pending")
        await enforce_concurrent_job_limit(s3, trial_session_id=ts_a.id)
        await s3.commit()

    # ── 4. completed / failed free a slot ───────────────────────────
    async with _test_session_factory() as s4:
        ts4 = await _trial_session(s4)
        for _ in range(L):
            await _job(s4, ts4.id, "completed")
        await enforce_concurrent_job_limit(s4, trial_session_id=ts4.id)
        await _job(s4, ts4.id, "failed")
        await enforce_concurrent_job_limit(s4, trial_session_id=ts4.id)
        await s4.commit()

    # ── 5. Concurrent race ──────────────────────────────────────────
    async with _test_session_factory() as s5:
        ts5 = await _trial_session(s5)
        for _ in range(L - 1):
            await _job(s5, ts5.id, "pending")
        await s5.commit()
        tsid5 = ts5.id

    async def _try5():
        try:
            await _gated_submit(tsid5)
            return "ok"
        except HTTPException:
            return "429"

    results = await asyncio.gather(_try5(), _try5())
    assert results.count("ok") == 1, results
    assert results.count("429") == 1, results

    async with _test_session_factory() as s5b:
        active = (await s5b.execute(
            sql_select(sql_func.count()).select_from(ProcessingJob).where(
                ProcessingJob.trial_session_id == tsid5,
                ProcessingJob.status.in_(("pending", "queued", "processing", "retrying"))
            )
        )).scalar_one()
        assert active == L, f"expected exactly {L} active, got {active}"
        await s5b.commit()

    await _test_engine.dispose()


@pytest.mark.asyncio(loop_scope="function")
async def test_rejects_both_ids_set():
    async with _test_session_factory() as s:
        with pytest.raises(ValueError):
            await enforce_concurrent_job_limit(s, user_id=str(uuid.uuid4()),
                                                trial_session_id=str(uuid.uuid4()))


@pytest.mark.asyncio(loop_scope="function")
async def test_rejects_neither_id_set():
    async with _test_session_factory() as s:
        with pytest.raises(ValueError):
            await enforce_concurrent_job_limit(s)
