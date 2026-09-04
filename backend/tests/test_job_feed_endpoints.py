"""Live-DB tests for the job-feed browse/import endpoints (item 7).
Mirrors test_job_post_collections_endpoints.py's pattern: own
create_async_engine(..., poolclass=NullPool), call route functions
directly, own fake Request object for rate-limit key extraction.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.job_feed import get_job_feed_posting, import_job_feed_posting, list_job_feed
from app.core.config import settings
from app.core.security import RequestIdentity
from app.db.models import FeedJobPosting, JobPost, ProcessingJob, TrialSession, User

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)

_ip_counter = 0


def _fake_request():
    global _ip_counter
    _ip_counter += 1
    return SimpleNamespace(client=SimpleNamespace(host=f"10.90.0.{_ip_counter}"), headers={})


async def _posting(session, *, source="remoteok", title="Backend Engineer", description=None):
    p = FeedJobPosting(
        id=str(uuid.uuid4()), source=source, external_id=uuid.uuid4().hex,
        title=title, company="Acme", location="Remote", remote=True,
        url=f"https://example.com/{uuid.uuid4().hex}",
        description=description if description is not None else ("Great role. " * 20),
    )
    session.add(p)
    await session.flush()
    return p


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


# ═══════════════════════════════════════════════════════════════════════
# GET /job-feed
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_list_job_feed_filters_by_query():
    async with _test_session_factory() as s:
        tag = uuid.uuid4().hex[:8]
        await _posting(s, title=f"UniqueBackendRole{tag}")
        await _posting(s, title=f"UnrelatedFrontendRole{tag}")
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_job_feed(
            request=_fake_request(), q=f"UniqueBackendRole{tag}", location=None, remote=None,
            source=None, limit=20, offset=0, session=s,
        )
        assert result.total == 1
        assert result.items[0].title == f"UniqueBackendRole{tag}"


@pytest.mark.asyncio(loop_scope="function")
async def test_list_job_feed_filters_by_source():
    async with _test_session_factory() as s:
        tag = uuid.uuid4().hex[:8]
        await _posting(s, source=f"src-a-{tag}")
        await _posting(s, source=f"src-b-{tag}")
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_job_feed(
            request=_fake_request(), q=None, location=None, remote=None,
            source=f"src-a-{tag}", limit=20, offset=0, session=s,
        )
        assert result.total == 1
        assert result.items[0].source == f"src-a-{tag}"


# ═══════════════════════════════════════════════════════════════════════
# GET /job-feed/{id}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_get_job_feed_posting_404_for_unknown_id():
    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await get_job_feed_posting(feedPostingId=str(uuid.uuid4()), session=s)
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /job-feed/{id}/import
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_import_creates_owned_job_post_from_authenticated_user():
    async with _test_session_factory() as s:
        user = await _user(s, "imp1")
        posting = await _posting(s)
        await s.commit()
        posting_id, posting_url = posting.id, posting.url

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        result = await import_job_feed_posting(
            feedPostingId=posting_id, request=_fake_request(), identity=identity, session=s,
        )
        assert result.jobPostId
        assert result.processingJobId

    async with _test_session_factory() as verify_s:
        jp_result = await verify_s.execute(select(JobPost).where(JobPost.id == result.jobPostId))
        jp = jp_result.scalar_one()
        assert jp.user_id == user.id
        assert jp.source_url == posting_url
        assert jp.source_type == "text"

        job_result = await verify_s.execute(
            select(ProcessingJob).where(ProcessingJob.source_entity_id == jp.id)
        )
        job = job_result.scalar_one()
        assert job.job_type == "job_post_parse"


@pytest.mark.asyncio(loop_scope="function")
async def test_import_works_for_trial_session_identity():
    async with _test_session_factory() as s:
        trial = TrialSession(
            id=str(uuid.uuid4()),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        s.add(trial)
        posting = await _posting(s)
        await s.commit()
        posting_id, trial_id = posting.id, trial.id

    async with _test_session_factory() as s:
        trial_result = await s.execute(select(TrialSession).where(TrialSession.id == trial_id))
        identity = RequestIdentity(user=None, trial_session=trial_result.scalar_one())
        result = await import_job_feed_posting(
            feedPostingId=posting_id, request=_fake_request(), identity=identity, session=s,
        )

    async with _test_session_factory() as verify_s:
        jp_result = await verify_s.execute(select(JobPost).where(JobPost.id == result.jobPostId))
        jp = jp_result.scalar_one()
        assert jp.trial_session_id == trial_id
        assert jp.user_id is None


@pytest.mark.asyncio(loop_scope="function")
async def test_import_rejects_too_short_description():
    async with _test_session_factory() as s:
        user = await _user(s, "imp3")
        posting = await _posting(s, description="Too short")
        await s.commit()
        posting_id = posting.id

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await import_job_feed_posting(
                feedPostingId=posting_id, request=_fake_request(), identity=identity, session=s,
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="function")
async def test_import_404s_for_unknown_posting():
    async with _test_session_factory() as s:
        user = await _user(s, "imp4")
        await s.commit()

    async with _test_session_factory() as s:
        identity = RequestIdentity(user=user, trial_session=None)
        with pytest.raises(HTTPException) as exc:
            await import_job_feed_posting(
                feedPostingId=str(uuid.uuid4()), request=_fake_request(), identity=identity, session=s,
            )
        assert exc.value.status_code == 404
