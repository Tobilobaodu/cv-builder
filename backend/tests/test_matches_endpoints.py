"""Live-DB tests for GET /matches (list) — the endpoint added alongside the
create_processing_job commit-before-enqueue fix.

Mirrors test_cover_letters_endpoints.py's pattern exactly: own
create_async_engine(..., poolclass=NullPool), no conftest.py, call the
route function directly (async, no TestClient/HTTP layer).
"""
import uuid

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.api.v1.matches import list_matches
from app.db.models import CvFile, CvProfileVersion, JobPost, JobPostProfile, MatchRun, User

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


async def _cv_profile_version(session, user):
    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=user.id, filename="cv.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="parsed",
    )
    session.add(cv_file)
    await session.flush()
    pv = CvProfileVersion(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, user_id=user.id, version_number=1,
        profile_hash=uuid.uuid4().hex, schema_version="1.0",
        structured_payload={"basics": {"name": "Jane Doe"}},
    )
    session.add(pv)
    await session.flush()
    return pv


async def _job_post_profile(session, user, *, job_title="Engineer", employer="Acme"):
    job_post = JobPost(
        id=str(uuid.uuid4()), user_id=user.id, source_type="text",
        raw_text="Python engineer wanted" * 10, status="structured",
    )
    session.add(job_post)
    await session.flush()
    jp_profile = JobPostProfile(
        id=str(uuid.uuid4()), job_post_id=job_post.id, job_title=job_title, employer=employer,
        required_skills=["Python"], preferred_skills=[],
    )
    session.add(jp_profile)
    await session.flush()
    return jp_profile


async def _match_run(session, user, *, status="completed", score=0.5, job_title="Engineer", employer="Acme"):
    pv = await _cv_profile_version(session, user)
    jp = await _job_post_profile(session, user, job_title=job_title, employer=employer)
    match = MatchRun(
        id=str(uuid.uuid4()), user_id=user.id, cv_profile_version_id=pv.id,
        job_post_profile_id=jp.id, status=status, score=score,
    )
    session.add(match)
    await session.flush()
    return match


@pytest.mark.asyncio(loop_scope="function")
async def test_list_matches_returns_job_title_and_employer_from_the_joined_profile():
    async with _test_session_factory() as s:
        user = await _user(s, "list1")
        await _match_run(s, user, job_title="Senior Engineer", employer="Acme Corp")
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_matches(limit=20, offset=0, current_user=user, session=s)

    assert result.total == 1
    assert len(result.items) == 1
    item = result.items[0]
    assert item.job_title == "Senior Engineer"
    assert item.employer == "Acme Corp"
    assert item.status == "completed"
    # match_runs.score is stored 0.0-1.0; the API converts to the same
    # 0-100 scale CvAnalysis.overall_score uses (see matches.py::_score_out).
    assert item.score == 50.0


@pytest.mark.asyncio(loop_scope="function")
async def test_list_matches_only_returns_the_current_users_own_matches():
    async with _test_session_factory() as s:
        user_a = await _user(s, "listA")
        user_b = await _user(s, "listB")
        await _match_run(s, user_a)
        await _match_run(s, user_b)
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_matches(limit=20, offset=0, current_user=user_a, session=s)

    assert result.total == 1
    assert len(result.items) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_list_matches_empty_for_a_user_with_no_matches():
    async with _test_session_factory() as s:
        user = await _user(s, "listempty")
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_matches(limit=20, offset=0, current_user=user, session=s)

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio(loop_scope="function")
async def test_list_matches_orders_most_recent_first_and_respects_pagination():
    async with _test_session_factory() as s:
        user = await _user(s, "listpage")
        first = await _match_run(s, user, job_title="First")
        second = await _match_run(s, user, job_title="Second")
        await s.commit()

    async with _test_session_factory() as s:
        page1 = await list_matches(limit=1, offset=0, current_user=user, session=s)
        page2 = await list_matches(limit=1, offset=1, current_user=user, session=s)

    assert page1.total == 2
    assert len(page1.items) == 1
    assert len(page2.items) == 1
    # Most recently created first — "second" was created after "first".
    assert page1.items[0].id == second.id
    assert page2.items[0].id == first.id
