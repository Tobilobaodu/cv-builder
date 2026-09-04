"""Live-DB tests for GET /audit/{entityType}/{entityId} (Sprint 6, Workstream F).

Mirrors the established endpoint-test pattern (own NullPool engine, no
conftest.py, call the route function directly). Proves: own-entity access
returns real audit rows; the response serializes to camelCase (this codebase
has shipped the inverse alias bug three separate times — verified directly,
not assumed); invalid entityType is rejected 422; and the cross-user case
returns an empty list (folded into test_idor_matrix.py as well).
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.api.v1.audit import get_audit_events
from app.db.models import AuditEvent, User

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


async def _event(session, user, *, event_type="upload", entity_type="cv_file"):
    e = AuditEvent(
        id=str(uuid.uuid4()), user_id=user.id, event_type=event_type,
        entity_type=entity_type, entity_id=str(uuid.uuid4()), actor_type="user",
        metadata_={"size": 123}, ip_address="203.0.113.9",
    )
    session.add(e)
    await session.flush()
    return e


@pytest.mark.asyncio(loop_scope="function")
async def test_own_entity_returns_events():
    async with _test_session_factory() as s:
        user = await _user(s, "auditown")
        e = await _event(s, user)
        entity_id = e.entity_id
        await s.commit()

    async with _test_session_factory() as s:
        events = await get_audit_events(
            entityType="cv_file", entityId=entity_id, limit=200, offset=0,
            current_user=user, session=s,
        )

    assert len(events) == 1
    assert events[0].id == e.id
    assert events[0].event_type == "upload"
    assert events[0].actor_type == "user"
    assert events[0].metadata == {"size": 123}


@pytest.mark.asyncio(loop_scope="function")
async def test_serialization_is_camel_case():
    async with _test_session_factory() as s:
        user = await _user(s, "auditser")
        e = await _event(s, user)
        await s.commit()

    async with _test_session_factory() as s:
        events = await get_audit_events(
            entityType="cv_file", entityId=e.entity_id, limit=200, offset=0,
            current_user=user, session=s,
        )

    dumped = events[0].model_dump(by_alias=True)
    assert "entityType" in dumped
    assert "entityId" in dumped
    assert "eventType" in dumped
    assert "actorType" in dumped
    assert "createdAt" in dumped
    assert "entity_type" not in dumped and "created_at" not in dumped, (
        "snake_case keys leaked into the serialized response"
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_invalid_entity_type_rejected_422():
    async with _test_session_factory() as s:
        user = await _user(s, "auditbadtype")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await get_audit_events(
                entityType="; DROP TABLE audit_events; --", entityId=str(uuid.uuid4()),
                limit=200, offset=0,
                current_user=user, session=s,
            )
        assert exc.value.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_cross_user_returns_empty_not_data():
    async with _test_session_factory() as s:
        owner = await _user(s, "auditowner")
        e = await _event(s, owner)
        await s.commit()
    async with _test_session_factory() as s:
        attacker = await _user(s, "auditattacker")
        await s.commit()

    async with _test_session_factory() as s:
        events = await get_audit_events(
            entityType="cv_file", entityId=e.entity_id, limit=200, offset=0,
            current_user=attacker, session=s,
        )

    assert events == []
