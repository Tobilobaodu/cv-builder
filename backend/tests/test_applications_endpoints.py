"""Live-DB tests for the applications endpoints (D5, 6a/6b). Mirrors
test_job_post_collections_endpoints.py's pattern: own
create_async_engine(..., poolclass=NullPool), no conftest.py fixtures
beyond the session-scoped DB isolation, call route functions directly.
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.applications import (
    add_application_note,
    create_application,
    delete_application,
    get_application,
    get_application_stats,
    list_applications,
    update_application_status,
)
from app.core.config import settings
from app.db.models import Application, ApplicationEvent, JobPost, User
from app.schemas.application import (
    AddApplicationNoteRequest,
    CreateApplicationRequest,
    UpdateApplicationStatusRequest,
)

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


async def _job_post(session, user):
    jp = JobPost(
        id=str(uuid.uuid4()), user_id=user.id, source_type="text",
        raw_text="Python engineer wanted" * 5, status="structured",
    )
    session.add(jp)
    await session.flush()
    return jp


# ═══════════════════════════════════════════════════════════════════════
# POST /applications
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_create_application_manual_entry_needs_no_job_post():
    async with _test_session_factory() as s:
        user = await _user(s, "create1")
        await s.commit()

    async with _test_session_factory() as s:
        result = await create_application(
            body=CreateApplicationRequest(jobTitle="Backend Engineer", employer="Acme Co"),
            current_user=user, session=s,
        )
        assert result.job_title == "Backend Engineer"
        assert result.employer == "Acme Co"
        assert result.status == "applied"
        assert len(result.events) == 1
        assert result.events[0].event_type == "status_change"
        assert result.events[0].to_status == "applied"


@pytest.mark.asyncio(loop_scope="function")
async def test_create_application_linked_to_owned_job_post():
    async with _test_session_factory() as s:
        user = await _user(s, "create2")
        jp = await _job_post(s, user)
        await s.commit()
        jp_id = jp.id

    async with _test_session_factory() as s:
        result = await create_application(
            body=CreateApplicationRequest(jobPostId=jp_id, jobTitle="Data Engineer", employer="Beta Inc"),
            current_user=user, session=s,
        )
        assert result.job_post_id == jp_id


@pytest.mark.asyncio(loop_scope="function")
async def test_create_application_rejects_job_post_owned_by_someone_else():
    async with _test_session_factory() as s:
        owner = await _user(s, "create3owner")
        jp = await _job_post(s, owner)
        await s.commit()
        jp_id = jp.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "create3attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await create_application(
                body=CreateApplicationRequest(jobPostId=jp_id, jobTitle="X", employer="Y"),
                current_user=attacker, session=s,
            )
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /applications
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_list_applications_scoped_to_current_user():
    async with _test_session_factory() as s:
        user = await _user(s, "list1")
        other = await _user(s, "list1other")
        await s.commit()

    async with _test_session_factory() as s:
        await create_application(
            body=CreateApplicationRequest(jobTitle="Mine", employer="A"), current_user=user, session=s,
        )
    async with _test_session_factory() as s:
        await create_application(
            body=CreateApplicationRequest(jobTitle="NotMine", employer="B"), current_user=other, session=s,
        )

    async with _test_session_factory() as s:
        result = await list_applications(limit=20, offset=0, status=None, current_user=user, session=s)
        assert result.total == 1
        assert result.items[0].job_title == "Mine"


@pytest.mark.asyncio(loop_scope="function")
async def test_list_applications_filters_by_status():
    async with _test_session_factory() as s:
        user = await _user(s, "list2")
        await s.commit()

    async with _test_session_factory() as s:
        a1 = await create_application(
            body=CreateApplicationRequest(jobTitle="A", employer="A"), current_user=user, session=s,
        )
    async with _test_session_factory() as s:
        await create_application(
            body=CreateApplicationRequest(jobTitle="B", employer="B"), current_user=user, session=s,
        )
    async with _test_session_factory() as s:
        await update_application_status(
            applicationId=a1.id, body=UpdateApplicationStatusRequest(status="interviewing"),
            current_user=user, session=s,
        )

    async with _test_session_factory() as s:
        result = await list_applications(limit=20, offset=0, status="interviewing", current_user=user, session=s)
        assert result.total == 1
        assert result.items[0].id == a1.id


# ═══════════════════════════════════════════════════════════════════════
# PATCH /applications/{id}/status
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_update_status_valid_transition_writes_event():
    async with _test_session_factory() as s:
        user = await _user(s, "status1")
        await s.commit()

    async with _test_session_factory() as s:
        app = await create_application(
            body=CreateApplicationRequest(jobTitle="X", employer="Y"), current_user=user, session=s,
        )

    async with _test_session_factory() as s:
        result = await update_application_status(
            applicationId=app.id,
            body=UpdateApplicationStatusRequest(status="interviewing", note="Phone screen scheduled"),
            current_user=user, session=s,
        )
        assert result.status == "interviewing"
        assert len(result.events) == 2
        assert result.events[-1].from_status == "applied"
        assert result.events[-1].to_status == "interviewing"
        assert result.events[-1].note == "Phone screen scheduled"


@pytest.mark.asyncio(loop_scope="function")
async def test_update_status_invalid_transition_400s():
    async with _test_session_factory() as s:
        user = await _user(s, "status2")
        await s.commit()

    async with _test_session_factory() as s:
        app = await create_application(
            body=CreateApplicationRequest(jobTitle="X", employer="Y"), current_user=user, session=s,
        )

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await update_application_status(
                applicationId=app.id, body=UpdateApplicationStatusRequest(status="accepted"),
                current_user=user, session=s,
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio(loop_scope="function")
async def test_update_status_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "status3owner")
        await s.commit()

    async with _test_session_factory() as s:
        app = await create_application(
            body=CreateApplicationRequest(jobTitle="X", employer="Y"), current_user=owner, session=s,
        )

    async with _test_session_factory() as s:
        attacker = await _user(s, "status3attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await update_application_status(
                applicationId=app.id, body=UpdateApplicationStatusRequest(status="interviewing"),
                current_user=attacker, session=s,
            )
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /applications/{id}/notes
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_add_note_does_not_change_status():
    async with _test_session_factory() as s:
        user = await _user(s, "note1")
        await s.commit()

    async with _test_session_factory() as s:
        app = await create_application(
            body=CreateApplicationRequest(jobTitle="X", employer="Y"), current_user=user, session=s,
        )

    async with _test_session_factory() as s:
        result = await add_application_note(
            applicationId=app.id, body=AddApplicationNoteRequest(note="Recruiter called"),
            current_user=user, session=s,
        )
        assert result.status == "applied"
        assert len(result.events) == 2
        assert result.events[-1].event_type == "note_added"
        assert result.events[-1].note == "Recruiter called"


# ═══════════════════════════════════════════════════════════════════════
# GET /applications/{id}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_get_application_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "get1owner")
        await s.commit()

    async with _test_session_factory() as s:
        app = await create_application(
            body=CreateApplicationRequest(jobTitle="X", employer="Y"), current_user=owner, session=s,
        )

    async with _test_session_factory() as s:
        attacker = await _user(s, "get1attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await get_application(applicationId=app.id, current_user=attacker, session=s)
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_get_application_404s_after_delete():
    async with _test_session_factory() as s:
        user = await _user(s, "get2")
        await s.commit()

    async with _test_session_factory() as s:
        app = await create_application(
            body=CreateApplicationRequest(jobTitle="X", employer="Y"), current_user=user, session=s,
        )

    async with _test_session_factory() as s:
        await delete_application(applicationId=app.id, current_user=user, session=s)

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await get_application(applicationId=app.id, current_user=user, session=s)
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /applications/stats
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_stats_response_rate_excludes_withdrawn_and_applied():
    async with _test_session_factory() as s:
        user = await _user(s, "stats1")
        await s.commit()

    async with _test_session_factory() as s:
        a1 = await create_application(
            body=CreateApplicationRequest(jobTitle="A", employer="A"), current_user=user, session=s,
        )
    async with _test_session_factory() as s:
        a2 = await create_application(
            body=CreateApplicationRequest(jobTitle="B", employer="B"), current_user=user, session=s,
        )
    async with _test_session_factory() as s:
        await create_application(  # stays "applied" — no response
            body=CreateApplicationRequest(jobTitle="C", employer="C"), current_user=user, session=s,
        )

    async with _test_session_factory() as s:
        await update_application_status(
            applicationId=a1.id, body=UpdateApplicationStatusRequest(status="interviewing"),
            current_user=user, session=s,
        )
    async with _test_session_factory() as s:
        await update_application_status(
            applicationId=a2.id, body=UpdateApplicationStatusRequest(status="withdrawn"),
            current_user=user, session=s,
        )

    async with _test_session_factory() as s:
        stats = await get_application_stats(current_user=user, session=s)
        assert stats.total == 3
        # 1 responded (a1 -> interviewing) out of 3 total
        assert stats.response_rate == pytest.approx(1 / 3)


@pytest.mark.asyncio(loop_scope="function")
async def test_stats_response_rate_none_when_no_applications():
    async with _test_session_factory() as s:
        user = await _user(s, "stats2")
        await s.commit()

    async with _test_session_factory() as s:
        stats = await get_application_stats(current_user=user, session=s)
        assert stats.total == 0
        assert stats.response_rate is None
