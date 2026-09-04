"""Live-DB tests for the tailored CV generation endpoints.

Mirrors test_job_concurrency_limit.py's / test_ats_check_live.py's
pattern: own create_async_engine(..., poolclass=NullPool), no
conftest.py, call the route functions directly (async, no TestClient —
this codebase has a documented asyncpg/TestClient event-loop
incompatibility). Does NOT invoke the Celery worker or the real OpenAI
API — process_cv_generate's own logic is covered by
test_tailored_cv_generation.py's fake-LLM-client tests; this file proves
the DB wiring, ownership, and status-transition logic around it.

Each test uses a distinct fake client IP for rate-limiting, since
check_generation_rate_limit's state is a shared module-level dict keyed
by client IP — reusing one IP across many tests in one pytest session
risks a spurious 429 once the tier's window fills up.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import RequestIdentity
from app.api.v1.tailored_cvs import (
    create_tailored_cv, get_tailored_cv, regenerate_tailored_cv, approve_tailored_cv,
)
from app.schemas.tailored_cv import RegenerateRequest
from app.db.models import (
    CvFile, CvProfileVersion, JobPost, JobPostProfile, MatchRun,
    ProcessingJob, TailoredCvDraft, TailoredCvSection, TrialSession, User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)

_ip_counter = 0


def _fake_request():
    """Unique fake IP per call — see module docstring on shared rate-limit state."""
    global _ip_counter
    _ip_counter += 1
    return SimpleNamespace(
        client=SimpleNamespace(host=f"10.77.0.{_ip_counter}"),
        headers={},
    )


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


async def _trial_session(session):
    from datetime import datetime, timedelta, timezone
    ts = TrialSession(
        id=str(uuid.uuid4()), expires_at=datetime.now(timezone.utc) + timedelta(hours=48),
    )
    session.add(ts)
    await session.flush()
    return ts


async def _match_run(session, *, user_id=None, trial_session_id=None, status="completed"):
    """Full chain: CvFile -> CvProfileVersion, JobPost -> JobPostProfile, MatchRun."""
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
        structured_payload={"basics": {}}, **owner,
    )
    session.add(pv)
    await session.flush()

    job_post = JobPost(
        id=str(uuid.uuid4()), source_type="text", raw_text="Python engineer wanted",
        status="structured", **owner,
    )
    session.add(job_post)
    await session.flush()

    jp_profile = JobPostProfile(
        id=str(uuid.uuid4()), job_post_id=job_post.id,
        required_skills=["Python"], preferred_skills=[],
    )
    session.add(jp_profile)
    await session.flush()

    match_run = MatchRun(
        id=str(uuid.uuid4()), cv_profile_version_id=pv.id, job_post_profile_id=jp_profile.id,
        status=status, **owner,
    )
    session.add(match_run)
    await session.flush()

    return match_run


async def _draft(session, match_run, *, user_id=None, trial_session_id=None,
                  status="generated", version_number=1):
    d = TailoredCvDraft(
        id=str(uuid.uuid4()), match_run_id=match_run.id, version_number=version_number,
        status=status, content_json={}, user_id=user_id, trial_session_id=trial_session_id,
    )
    session.add(d)
    await session.flush()
    return d


# ═══════════════════════════════════════════════════════════════════════
# POST /matches/{matchId}/tailored-cv
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_post_creates_pending_draft_and_job():
    async with _test_session_factory() as s:
        user = await _user(s, "post1")
        match_run = await _match_run(s, user_id=user.id, status="completed")
        await s.commit()
        match_id = match_run.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        result = await create_tailored_cv(
            matchId=match_id, request=_fake_request(), identity=identity, session=s,
        )
        assert result.status == "queued"

    async with _test_session_factory() as verify_s:
        draft_result = await verify_s.execute(
            select(TailoredCvDraft).where(TailoredCvDraft.match_run_id == match_id)
        )
        draft = draft_result.scalar_one()
        assert draft.status == "pending"
        assert draft.version_number == 1

        job_result = await verify_s.execute(
            select(ProcessingJob).where(ProcessingJob.source_entity_id == draft.id)
        )
        job = job_result.scalar_one()
        assert job.job_type == "cv_generate"
        assert job.source_entity_type == "tailored_cv_draft"


@pytest.mark.asyncio(loop_scope="function")
async def test_post_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "post2owner")
        match_run = await _match_run(s, user_id=owner.id, status="completed")
        await s.commit()
        match_id = match_run.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "post2attacker")
        await s.commit()

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=attacker, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await create_tailored_cv(
                matchId=match_id, request=_fake_request(), identity=identity, session=s,
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_post_rejects_non_completed_match():
    async with _test_session_factory() as s:
        user = await _user(s, "post3")
        match_run = await _match_run(s, user_id=user.id, status="pending")
        await s.commit()
        match_id = match_run.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await create_tailored_cv(
                matchId=match_id, request=_fake_request(), identity=identity, session=s,
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio(loop_scope="function")
async def test_post_rejects_duplicate_draft():
    async with _test_session_factory() as s:
        user = await _user(s, "post4")
        match_run = await _match_run(s, user_id=user.id, status="completed")
        await s.commit()
        match_id = match_run.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        await create_tailored_cv(matchId=match_id, request=_fake_request(), identity=identity, session=s)

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await create_tailored_cv(
                matchId=match_id, request=_fake_request(), identity=identity, session=s,
            )
        assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# GET /tailored-cvs/{draftId}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_get_returns_draft_with_sections():
    async with _test_session_factory() as s:
        user = await _user(s, "get1")
        match_run = await _match_run(s, user_id=user.id)
        draft = await _draft(s, match_run, user_id=user.id, status="generated")
        s.add(TailoredCvSection(
            id=str(uuid.uuid4()), draft_id=draft.id, section_type="summary",
            content_text="Experienced Python engineer.", evidence_references=["sk1"],
            generation_task="tailored_cv_summary", prompt_version="v1", model_id="gpt-4o-mini",
            validation_status="passed", order_index=0,
        ))
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        response = await get_tailored_cv(draftId=draft_id, identity=identity, session=s)
        assert response.id == draft_id
        assert response.status == "generated"
        assert len(response.sections) == 1
        assert response.sections[0].content_text == "Experienced Python engineer."
        assert response.sections[0].evidence_references == ["sk1"]


@pytest.mark.asyncio(loop_scope="function")
async def test_get_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "get2owner")
        match_run = await _match_run(s, user_id=owner.id)
        draft = await _draft(s, match_run, user_id=owner.id)
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "get2attacker")
        await s.commit()

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=attacker, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await get_tailored_cv(draftId=draft_id, identity=identity, session=s)
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_get_nonexistent_draft_404s():
    async with _test_session_factory() as s:
        user = await _user(s, "get3")
        await s.commit()
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await get_tailored_cv(draftId=str(uuid.uuid4()), identity=identity, session=s)
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /tailored-cvs/{draftId}/regenerate
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_regenerate_creates_new_version_leaves_original_untouched():
    async with _test_session_factory() as s:
        user = await _user(s, "regen1")
        match_run = await _match_run(s, user_id=user.id)
        base_draft = await _draft(s, match_run, user_id=user.id, status="generated", version_number=1)
        await s.commit()
        base_draft_id = base_draft.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        result = await regenerate_tailored_cv(
            draftId=base_draft_id, request=_fake_request(),
            body=RegenerateRequest(instructions="emphasise leadership"),
            identity=identity, session=s,
        )
        assert result.status == "queued"

    async with _test_session_factory() as verify_s:
        # Original untouched — "old version stays retrievable" per 09-test-plan.md §6.
        original = await verify_s.get(TailoredCvDraft, base_draft_id)
        assert original.status == "generated"
        assert original.version_number == 1

        new_result = await verify_s.execute(
            select(TailoredCvDraft).where(
                TailoredCvDraft.match_run_id == match_run.id,
                TailoredCvDraft.id != base_draft_id,
            )
        )
        new_draft = new_result.scalar_one()
        assert new_draft.version_number == 2
        assert new_draft.status == "pending"
        assert new_draft.instructions == "emphasise leadership"


@pytest.mark.asyncio(loop_scope="function")
async def test_regenerate_rejects_pending_state():
    async with _test_session_factory() as s:
        user = await _user(s, "regen2")
        match_run = await _match_run(s, user_id=user.id)
        draft = await _draft(s, match_run, user_id=user.id, status="pending")
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await regenerate_tailored_cv(
                draftId=draft_id, request=_fake_request(),
                body=RegenerateRequest(instructions=None), identity=identity, session=s,
            )
        assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# POST /tailored-cvs/{draftId}/approve
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_approve_succeeds_from_generated_state():
    async with _test_session_factory() as s:
        user = await _user(s, "approve1")
        match_run = await _match_run(s, user_id=user.id)
        draft = await _draft(s, match_run, user_id=user.id, status="generated")
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        response = await approve_tailored_cv(draftId=draft_id, identity=identity, session=s)
        assert response.status == "approved"
        assert response.approved_at is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_approve_rejects_pending_state():
    async with _test_session_factory() as s:
        user = await _user(s, "approve2")
        match_run = await _match_run(s, user_id=user.id)
        draft = await _draft(s, match_run, user_id=user.id, status="pending")
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await approve_tailored_cv(draftId=draft_id, identity=identity, session=s)
        assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# Trial-session access (Sprint 2 identity pattern)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_trial_session_can_create_and_read_own_draft():
    async with _test_session_factory() as s:
        ts = await _trial_session(s)
        match_run = await _match_run(s, trial_session_id=ts.id, status="completed")
        await s.commit()
        match_id = match_run.id
        ts_id = ts.id

    async with _test_session_factory() as s:
        ts_row = await s.get(TrialSession, ts_id)
        identity = RequestIdentity(user=None, trial_session=ts_row)
        result = await create_tailored_cv(
            matchId=match_id, request=_fake_request(), identity=identity, session=s,
        )
        assert result.status == "queued"

    async with _test_session_factory() as verify_s:
        draft_result = await verify_s.execute(
            select(TailoredCvDraft).where(TailoredCvDraft.match_run_id == match_id)
        )
        draft = draft_result.scalar_one()
        assert draft.trial_session_id == ts_id
        assert draft.user_id is None
        draft_id = draft.id

    async with _test_session_factory() as s:
        ts_row = await s.get(TrialSession, ts_id)
        identity = RequestIdentity(user=None, trial_session=ts_row)
        response = await get_tailored_cv(draftId=draft_id, identity=identity, session=s)
        assert response.id == draft_id


@pytest.mark.asyncio(loop_scope="function")
async def test_trial_session_cannot_access_users_draft():
    async with _test_session_factory() as s:
        user = await _user(s, "trialgap")
        match_run = await _match_run(s, user_id=user.id)
        draft = await _draft(s, match_run, user_id=user.id)
        await s.commit()
        draft_id = draft.id

    async with _test_session_factory() as s:
        ts = await _trial_session(s)
        await s.commit()
        ts_id = ts.id

    async with _test_session_factory() as s:
        ts_row = await s.get(TrialSession, ts_id)
        identity = RequestIdentity(user=None, trial_session=ts_row)
        with pytest.raises(HTTPException) as exc:
            await get_tailored_cv(draftId=draft_id, identity=identity, session=s)
        assert exc.value.status_code == 404
