import asyncio, uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import update as sa_update
from sqlalchemy import func as sql_func, select as sql_select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.services.orchestration import enforce_concurrent_job_limit, mark_job_publish_failed
from app.db.models import User, ProcessingJob

# Dedicated NullPool engine for this test file only, so each session
# checkout is a fresh connection with no pooled state carried between
# scenarios.  Does not affect the application's pooled engine in
# session.py.  Test-only.
_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


async def _user(session, tag=""):
    u = User(id=str(uuid.uuid4()),
             email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
             password_hash="fake", status="active")
    session.add(u)
    await session.flush()
    return u


async def _job(session, uid, status="pending"):
    j = ProcessingJob(id=str(uuid.uuid4()), user_id=uid, job_type="match",
                      source_entity_type="match_run",
                      source_entity_id=str(uuid.uuid4()), status=status)
    session.add(j)
    await session.flush()
    return j


async def _gated_submit(user_id, job_status="pending"):
    async with _test_session_factory() as s:
        await enforce_concurrent_job_limit(s, user_id)
        j = ProcessingJob(id=str(uuid.uuid4()), user_id=user_id,
                          job_type="match", source_entity_type="match_run",
                          source_entity_id=str(uuid.uuid4()),
                          status=job_status)
        s.add(j)
        await s.flush()
        await s.commit()
        return "ok"


@pytest.mark.asyncio(loop_scope="function")
async def test_all_scenarios():
    L = settings.max_concurrent_jobs_per_user

    # ── 1. Below-limit allowed ──────────────────────────────────────
    async with _test_session_factory() as s1:
        u1 = await _user(s1)
        for _ in range(L - 1):
            await _job(s1, u1.id, "pending")
        await enforce_concurrent_job_limit(s1, u1.id)
        await s1.commit()

    # ── 2. At-limit → 429 ───────────────────────────────────────────
    got_429 = False
    async with _test_session_factory() as s2:
        u2 = await _user(s2)
        for _ in range(L):
            await _job(s2, u2.id, "pending")
        try:
            await enforce_concurrent_job_limit(s2, u2.id)
            await s2.commit()
            pytest.fail("expected 429")
        except HTTPException as e:
            got_429 = True
            assert e.status_code == 429
            assert "Too many active" in e.detail
    assert got_429

    # ── 3. Other users not counted ──────────────────────────────────
    async with _test_session_factory() as s3:
        a = await _user(s3, "a")
        b = await _user(s3, "b")
        for _ in range(L):
            await _job(s3, b.id, "pending")
        await enforce_concurrent_job_limit(s3, a.id)
        await s3.commit()

    # ── 4. completed / failed free slot ─────────────────────────────
    async with _test_session_factory() as s4:
        u4 = await _user(s4)
        for _ in range(L):
            await _job(s4, u4.id, "completed")
        await enforce_concurrent_job_limit(s4, u4.id)
        await _job(s4, u4.id, "failed")
        await enforce_concurrent_job_limit(s4, u4.id)
        await s4.commit()

    # ── 5. Concurrent race ──────────────────────────────────────────
    async with _test_session_factory() as s5:
        u5 = await _user(s5)
        for _ in range(L - 1):
            await _job(s5, u5.id, "pending")
        await s5.commit()
        uid5 = u5.id

    async def _try5():
        try:
            await _gated_submit(uid5)
            return "ok"
        except HTTPException:
            return "429"

    results = await asyncio.gather(_try5(), _try5())
    assert results.count("ok") == 1, results
    assert results.count("429") == 1, results

    async with _test_session_factory() as s5b:
        active = (await s5b.execute(
            sql_select(sql_func.count()).select_from(ProcessingJob).where(
                ProcessingJob.user_id == uid5,
                ProcessingJob.status.in_(("pending", "queued", "processing", "retrying"))
            )
        )).scalar_one()
        assert active == L, f"expected exactly {L} active, got {active}"
        await s5b.commit()

    # ── 6. Publish-failure frees slot -- exercise the same
    # status transition the production publish-failure handlers use
    # (matches.py + job_posts.py try/except blocks).
    jid6 = None
    async with _test_session_factory() as s6:
        u6 = await _user(s6)
        job6 = await _job(s6, u6.id, "pending")
        jid6 = job6.id
        await s6.commit()
        uid6 = u6.id

    async with _test_session_factory() as s6b:
        job = await s6b.get(ProcessingJob, jid6)
        mark_job_publish_failed(job, "Failed to publish task to message broker.")
        await s6b.commit()

        assert job.status == "failed"
        assert "Failed to publish" in (job.last_error or "")

    # Slot freed: fill to limit-1, then one gated submit must succeed
    got_429_6 = False
    async with _test_session_factory() as s6c:
        for _ in range(L - 1):
            await _job(s6c, uid6, "pending")
        await s6c.commit()

    await _gated_submit(uid6)
    try:
        await _gated_submit(uid6)
        pytest.fail("expected 429")
    except HTTPException:
        got_429_6 = True
    assert got_429_6

    await _test_engine.dispose()
