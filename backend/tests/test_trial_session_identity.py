"""Sprint 2: get_current_user_or_trial_session()'s actual DB-touching
behavior, plus IDOR checks at the real query level via identity_owner_filter.

test_request_identity.py already covers the DB-independent half
(RequestIdentity's properties, identity_owner_filter's expression
building) with plain constructed objects — this file covers what that
one explicitly doesn't: the dependency's own TrialSession lookup and
expiry/claim/precedence logic against a live database.

Calls get_current_user_or_trial_session directly as a plain async
function (bypassing FastAPI's DI), with a SimpleNamespace fake request —
matching test_rate_limit_identity.py's existing pattern for get_client_key,
since both dependencies only ever touch request.headers / request.client.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import (
    create_access_token,
    get_current_user_or_trial_session,
    hash_password,
    hash_token,
    identity_owner_filter,
)
from app.db.models import CvFile, MatchRun, TrialSession, User, UserSession

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


def _request(headers=None):
    return SimpleNamespace(headers=headers or {})


async def _trial_session(session, expires_delta=timedelta(hours=24), claimed_by_user_id=None):
    ts = TrialSession(id=str(uuid.uuid4()),
                       expires_at=datetime.now(timezone.utc) + expires_delta,
                       claimed_by_user_id=claimed_by_user_id)
    session.add(ts)
    await session.flush()
    return ts


async def _user(session, tag=""):
    u = User(id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
             password_hash=hash_password("Password123!"), status="active")
    session.add(u)
    await session.flush()
    return u


async def _live_session_for(session, user, token):
    """Insert the user_sessions row get_current_user() now requires to
    accept `token` — mirrors what POST /auth/login does. Tests that mint a
    token via create_access_token() directly (bypassing the real login
    endpoint) need this or every call resolves to 401 'Session has been
    revoked or is invalid', since a bare JWT is no longer sufficient on
    its own."""
    us = UserSession(
        id=str(uuid.uuid4()), user_id=user.id,
        refresh_token_hash=uuid.uuid4().hex, access_token_hash=hash_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    session.add(us)
    await session.flush()
    return us


class TestTrialHeaderResolution:

    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_unclaimed_unexpired_trial_resolves(self):
        async with _test_session_factory() as s:
            ts = await _trial_session(s)
            await s.commit()
            identity = await get_current_user_or_trial_session(
                request=_request({"X-Trial-Session-Id": ts.id}),
                credentials=None,
                session=s,
            )
            assert identity.user is None
            assert identity.trial_session_id == ts.id

    @pytest.mark.asyncio(loop_scope="function")
    async def test_expired_trial_rejected(self):
        async with _test_session_factory() as s:
            ts = await _trial_session(s, expires_delta=timedelta(hours=-1))
            await s.commit()
            with pytest.raises(HTTPException) as exc:
                await get_current_user_or_trial_session(
                    request=_request({"X-Trial-Session-Id": ts.id}),
                    credentials=None,
                    session=s,
                )
            assert exc.value.status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_already_claimed_trial_rejected(self):
        async with _test_session_factory() as s:
            u = await _user(s, "claimer")
            ts = await _trial_session(s, claimed_by_user_id=u.id)
            await s.commit()
            with pytest.raises(HTTPException) as exc:
                await get_current_user_or_trial_session(
                    request=_request({"X-Trial-Session-Id": ts.id}),
                    credentials=None,
                    session=s,
                )
            assert exc.value.status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_nonexistent_trial_id_rejected(self):
        async with _test_session_factory() as s:
            with pytest.raises(HTTPException) as exc:
                await get_current_user_or_trial_session(
                    request=_request({"X-Trial-Session-Id": str(uuid.uuid4())}),
                    credentials=None,
                    session=s,
                )
            assert exc.value.status_code == 401

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_credentials_and_missing_header_rejected(self):
        async with _test_session_factory() as s:
            with pytest.raises(HTTPException) as exc:
                await get_current_user_or_trial_session(
                    request=_request({}),
                    credentials=None,
                    session=s,
                )
            assert exc.value.status_code == 401


class TestBearerPrecedence:

    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_bearer_wins_over_trial_header(self):
        async with _test_session_factory() as s:
            u = await _user(s, "bearer")
            ts = await _trial_session(s)
            token = create_access_token(u.id)
            await _live_session_for(s, u, token)
            await s.commit()
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            identity = await get_current_user_or_trial_session(
                request=_request({"X-Trial-Session-Id": ts.id}),
                credentials=creds,
                session=s,
            )
            assert identity.user_id == u.id
            assert identity.trial_session is None
            # The trial session itself must be untouched by this call.
            refreshed = (await s.execute(
                select(TrialSession).where(TrialSession.id == ts.id)
            )).scalar_one()
            assert refreshed.claimed_by_user_id is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_bearer_with_valid_trial_header_still_rejected(self):
        """Fail closed: a bad token must not silently fall back to
        anonymous just because a valid trial header is also present."""
        async with _test_session_factory() as s:
            ts = await _trial_session(s)
            await s.commit()
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-real-jwt")
            with pytest.raises(HTTPException) as exc:
                await get_current_user_or_trial_session(
                    request=_request({"X-Trial-Session-Id": ts.id}),
                    credentials=creds,
                    session=s,
                )
            assert exc.value.status_code == 401


class TestIdentityOwnerFilterIDOR:
    """identity_owner_filter used at the real query level — a trial
    session cannot read another trial session's (or a user's) rows."""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cv_file_not_visible_to_other_trial_session(self):
        async with _test_session_factory() as s:
            owner_ts = await _trial_session(s)
            other_ts = await _trial_session(s)
            cv = CvFile(id=str(uuid.uuid4()), trial_session_id=owner_ts.id,
                        filename="x.pdf", mime_type="application/pdf", file_size=1,
                        storage_key=str(uuid.uuid4()), status="pending")
            s.add(cv)
            await s.commit()

            owner_identity = await get_current_user_or_trial_session(
                request=_request({"X-Trial-Session-Id": owner_ts.id}), credentials=None, session=s,
            )
            other_identity = await get_current_user_or_trial_session(
                request=_request({"X-Trial-Session-Id": other_ts.id}), credentials=None, session=s,
            )

            found_by_owner = (await s.execute(
                select(CvFile).where(CvFile.id == cv.id, identity_owner_filter(CvFile, owner_identity))
            )).scalar_one_or_none()
            found_by_other = (await s.execute(
                select(CvFile).where(CvFile.id == cv.id, identity_owner_filter(CvFile, other_identity))
            )).scalar_one_or_none()

            assert found_by_owner is not None
            assert found_by_other is None

    @pytest.mark.asyncio(loop_scope="function")
    async def test_match_run_not_visible_to_other_trial_session(self):
        async with _test_session_factory() as s:
            owner_ts = await _trial_session(s)
            other_ts = await _trial_session(s)

            cv_file = CvFile(id=str(uuid.uuid4()), trial_session_id=owner_ts.id,
                              filename="x.pdf", mime_type="application/pdf", file_size=1,
                              storage_key=str(uuid.uuid4()), status="parsed")
            s.add(cv_file)
            await s.flush()

            from app.db.models import CvProfileVersion, JobPost, JobPostProfile
            pv = CvProfileVersion(id=str(uuid.uuid4()), cv_file_id=cv_file.id,
                                   trial_session_id=owner_ts.id, version_number=1,
                                   profile_hash=uuid.uuid4().hex, schema_version="1.0",
                                   structured_payload={})
            jp = JobPost(id=str(uuid.uuid4()), trial_session_id=owner_ts.id,
                         source_type="text", raw_text="x" * 150, status="completed")
            s.add_all([pv, jp])
            await s.flush()
            jpp = JobPostProfile(id=str(uuid.uuid4()), job_post_id=jp.id)
            s.add(jpp)
            await s.flush()

            match_run = MatchRun(id=str(uuid.uuid4()), trial_session_id=owner_ts.id,
                                  cv_profile_version_id=pv.id, job_post_profile_id=jpp.id,
                                  status="pending")
            s.add(match_run)
            await s.commit()

            other_identity = await get_current_user_or_trial_session(
                request=_request({"X-Trial-Session-Id": other_ts.id}), credentials=None, session=s,
            )
            found_by_other = (await s.execute(
                select(MatchRun).where(
                    MatchRun.id == match_run.id, identity_owner_filter(MatchRun, other_identity),
                )
            )).scalar_one_or_none()
            assert found_by_other is None
