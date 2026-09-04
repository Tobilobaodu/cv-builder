"""Authentication and security utilities.

- bcrypt password hashing (cost factor >= 12)
- JWT access token issuance and verification (short-lived, per spec)
- Refresh token generation (revocable, stored in user_sessions)
- get_current_user() checks both the JWT itself AND that a live,
  non-revoked user_sessions row backs it — a token surviving logout
  (revoked_at set) or an expired session is rejected immediately rather
  than remaining bearer-valid until its own JWT exp.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db import get_session
from app.db.models import AuditEvent, TrialSession, User, UserSession
from app.core.metrics import AUTH_FAILURE_COUNTER, AUTHZ_DENIED_COUNTER


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (cost factor >= 12 per security plan)."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its bcrypt hash. Timing-safe by bcrypt library."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(user_id: str) -> str:
    """Issue a short-lived JWT access token.

    Payload contains ONLY user_id and expiry — no PII, roles, or sensitive data
    (per security plan §1: JWT is base64, not encrypted).
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_expiry),
        # Unique per issuance. Without it the payload is a pure function of
        # (user_id, current second), so two tokens minted for the same user
        # inside the same second — two tabs redeeming a refresh token at
        # once, or a login racing a refresh — are byte-identical, and the
        # second write collides on user_sessions.access_token_hash's UNIQUE
        # index (a 500, not two working sessions). Never read back:
        # get_current_user only uses "sub", and an unknown claim is ignored
        # on decode, so this is not a token-format change for any consumer.
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT access token. Raises JWTError on failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])


def generate_refresh_token() -> str:
    """Generate a secure random refresh token for server-side storage."""
    return secrets.token_urlsafe(64)


def hash_token(token: str) -> str:
    """One-way hash a token for storage lookup (displayed token vs stored hash)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# Bearer token security scheme
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency: authenticate via Bearer token, return the User.

    Returns 401 for missing/invalid/expired tokens. Does NOT return 403 directly
    — that's the authorization layer's responsibility (IDOR checks per endpoint).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )

    token = credentials.credentials

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        AUTH_FAILURE_COUNTER.labels(reason="expired_token").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # A valid signature/expiry alone isn't enough — the session backing this
    # exact token must still be live. Without this check, logout's
    # revoked_at write (auth.py) is pure bookkeeping: the bearer token
    # itself stays usable until its own JWT exp, up to jwt_expiry seconds
    # later, contradicting the "invalidate on logout" requirement.
    session_result = await session.execute(
        select(UserSession).where(
            UserSession.access_token_hash == hash_token(token),
            UserSession.revoked_at.is_(None),
        )
    )
    user_session = session_result.scalar_one_or_none()

    if user_session is None:
        AUTH_FAILURE_COUNTER.labels(reason="revoked_token").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked or is invalid",
        )

    if user_session.expires_at <= datetime.now(timezone.utc):
        AUTH_FAILURE_COUNTER.labels(reason="revoked_token").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired",
        )

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is not active",
        )

    return user


# ── Anonymous trial support (Sprint 2) ─────────────────────────────────


@dataclass
class RequestIdentity:
    """Whichever identity resolved a request: a real user, or a trial
    session — never both, never neither. Lets the small set of
    trial-eligible routes write `identity.user_id`/`identity.trial_session_id`
    instead of branching on which one is set at every call site.
    """

    user: User | None
    trial_session: TrialSession | None

    @property
    def user_id(self) -> str | None:
        return self.user.id if self.user else None

    @property
    def trial_session_id(self) -> str | None:
        return self.trial_session.id if self.trial_session else None


async def ownership_denied(
    session: AsyncSession,
    *,
    user_id: str | None,
    entity_type: str,
    entity_id: str,
    detail: str,
) -> HTTPException:
    """Central IDOR-denial chokepoint.

    Every route that 404s because the requester doesn't own the resource
    should ``raise await ownership_denied(...)`` rather than raising
    HTTPException 404 directly — this counts the denial and audits it once,
    in one place, so the §10 IDOR-probing alert (prometheus/alert_rules.yml)
    and the audit trail are both wired regardless of which route raised it,
    instead of being scattered per-route where a new route can forget one.

    Writes an AuditEvent(event_type="access_denied") and commits it
    immediately — a tabletop exercise found audit_events previously only
    covered mutations, never denied-read attempts, so investigating "which
    resources did this account try to access" came back empty for a pure
    IDOR probe (only the aggregate counter had any signal). Must commit here
    rather than leaving it for the caller's own commit: the caller raises
    the returned exception immediately after, and get_session() closes
    (rolling back) without committing on an unhandled exception.

    ``user_id=None`` covers trial-session-only requests — AuditEvent.user_id
    is nullable for exactly this case (see existing mutation-event call
    sites, e.g. app/api/v1/job_posts.py's upload event).
    """
    AUTHZ_DENIED_COUNTER.inc()
    session.add(AuditEvent(
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type="access_denied",
        actor_type="user" if user_id else "trial_session",
    ))
    await session.commit()
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def identity_owner_filter(model, identity: RequestIdentity):
    """Build the ownership WHERE clause for a trial-eligible model given a
    RequestIdentity: `model.user_id == ...` or `model.trial_session_id ==
    ...`, whichever the identity actually resolved to. Shared by every
    route that reads back a resource created via
    get_current_user_or_trial_session, so the two-way branch lives in one
    place instead of being re-derived per route.
    """
    if identity.user_id is not None:
        return model.user_id == identity.user_id
    return model.trial_session_id == identity.trial_session_id


async def get_current_user_or_trial_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> RequestIdentity:
    """FastAPI dependency for the small set of routes that support both a
    real authenticated user and an anonymous trial session (Sprint 2:
    POST /cvs, POST /job-posts/url, POST /job-posts/text, POST /matches,
    and their read-back counterparts).

    Deliberately NOT a modification of get_current_user() above — that
    function is used throughout the authenticated API surface and its
    behavior must not change for this one new use case. This wraps it
    instead: a valid Bearer token always resolves via get_current_user()
    unchanged and wins over a trial header, so a logged-in user hitting
    one of these routes is never accidentally treated as anonymous.
    Falls back to the X-Trial-Session-Id header only when no Bearer
    credentials are present at all.

    Every route that should stay authenticated-only (dashboard, cover
    letters, company tracking) keeps using get_current_user directly and
    is unaffected by this function's existence. Sprint 5 adds exports as
    a partial exception: POST /exports/cv/{draftId} uses this dependency
    too, since tailored CV generation itself is already trial-accessible
    end to end and exporting a CV a trial identity was allowed to
    generate shouldn't hit an account wall mid-flow — but
    POST /exports/cover-letter/{workflowId} and
    POST /exports/application-pack stay get_current_user-only, since both
    require a CoverLetterWorkflow, which is itself account-only.
    """
    if credentials is not None:
        user = await get_current_user(request, credentials, session)
        return RequestIdentity(user=user, trial_session=None)

    trial_session_id = request.headers.get("X-Trial-Session-Id")
    if not trial_session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token or trial session.",
        )

    result = await session.execute(
        select(TrialSession).where(TrialSession.id == trial_session_id)
    )
    trial_session = result.scalar_one_or_none()

    if trial_session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid trial session.",
        )

    if trial_session.claimed_by_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Trial session has already been claimed — sign in instead.",
        )

    if trial_session.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Trial session has expired.",
        )

    return RequestIdentity(user=None, trial_session=trial_session)


# ── Row-Level Security session scoping ──────────────────────────────────
#
# Postgres RLS policies (alembic migration 018) read a transaction-local
# GUC, app.user_id, to decide which rows a query may see. Nothing sets
# that GUC anywhere else — every route touching an RLS-covered table
# depends on one of the two functions below instead of the bare
# get_session (see 018's docstring for the exact table list).
#
# _set_rls_scope uses set_config('app.user_id', value, true) rather than
# `SET LOCAL app.user_id = :value` — Postgres's SET statements don't
# accept bind parameters at all, so the literal-SET-LOCAL spelling can
# only take a hardcoded/string-formatted value, not a safely parameterized
# one. set_config()'s third argument (is_local=true) is otherwise
# identical: scoped to the current transaction, not the session, and it
# clears the instant that transaction ends. Across the routers, ~34 call
# sites call session.commit() mid-request, and SQLAlchemy auto-begins a
# new transaction on the next statement after a commit — so a GUC set
# once, before the route body runs, would silently stop applying after
# that route's first commit, re-opening exactly the gap RLS exists to
# close, but only on requests that happen to commit more than once.
# Wrapping session.commit() to re-issue it closes that without touching
# any of those 34 call sites individually.


async def _set_rls_scope(session: AsyncSession, guc_value: str) -> None:
    # set_config(), not `SET LOCAL app.user_id = :uid` — Postgres's SET
    # statements don't accept bind parameters at all (a hard syntax
    # limitation, not an asyncpg quirk: `SET LOCAL x = $1` is invalid SQL
    # regardless of driver). set_config()'s third argument (is_local=true)
    # gives the identical transaction-scoped-GUC behavior through an
    # ordinary, safely parameterized function call instead.
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"), {"uid": guc_value}
    )


def _rescope_after_commit(session: AsyncSession, guc_value: str) -> None:
    original_commit = session.commit

    async def _commit_and_rescope(*args, **kwargs):
        await original_commit(*args, **kwargs)
        await _set_rls_scope(session, guc_value)

    session.commit = _commit_and_rescope


async def get_scoped_session(
    session: AsyncSession = Depends(get_session),
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
) -> AsyncSession:
    """RLS-scoped session for the trial-eligible routes. Depend on this
    instead of get_session for any route reachable via
    get_current_user_or_trial_session that touches an RLS-covered table.
    """
    guc_value = str(identity.user_id or identity.trial_session_id)
    await _set_rls_scope(session, guc_value)
    _rescope_after_commit(session, guc_value)
    return session


async def get_scoped_session_for_user(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AsyncSession:
    """RLS-scoped session for the account-only routes (cover letters,
    coverage reports, job post collections) that use get_current_user
    directly rather than the trial-eligible dependency above.
    """
    guc_value = str(current_user.id)
    await _set_rls_scope(session, guc_value)
    _rescope_after_commit(session, guc_value)
    return session