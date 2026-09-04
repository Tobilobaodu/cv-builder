"""Auth endpoints — POST /auth/register, /auth/login, /auth/logout, GET /auth/me.

Matches 05-openapi.yaml exactly. Uses bcrypt for password hashing,
short-lived JWT access tokens, and revocable refresh tokens per security plan §1.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import AUTH_FAILURE_COUNTER
from app.core.rate_limit import check_rate_limit, get_client_key
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    get_current_user,
    verify_password,
)
from app.db import get_session
from app.db.models import AuditEvent, User, UserSession
from app.services.trial_session import claim_trial_session
from app.schemas.auth import (
    ClaimTrialRequest,
    ClaimTrialResponse,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RegisterRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)

# Precomputed once at import time (bcrypt cost 12, matching hash_password's
# default). Checked against on the "user not found" login path so it pays
# the same bcrypt cost as the real "wrong password" path — otherwise the
# two failure branches are distinguishable by response latency even though
# they return the same error message, letting an attacker enumerate valid
# emails via timing.
_DUMMY_PASSWORD_HASH = hash_password("dummy-value-never-compared-to-a-real-account")


def _map_user(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        account_status=user.status,
        created_at=user.created_at,
    )


async def _create_audit_event(
    session: AsyncSession,
    event_type: str,
    user_id: str | None,
    entity_type: str | None,
    entity_id: str | None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Create an append-only audit event."""
    event = AuditEvent(
        user_id=user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_type="user",
        metadata_=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(event)


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a new user account.

    Rate-limited per client IP (5 attempts per 60s window).
    Returns 409 if the email is already registered.
    Password is hashed with bcrypt (cost factor >= 12).
    """
    # Rate limit
    client_key = get_client_key(request)
    if not check_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Please wait and try again.",
        )

    # Check for duplicate email
    existing = await session.execute(
        select(User).where(User.email == body.email)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
    )
    session.add(user)
    await session.flush()

    await _create_audit_event(
        session=session,
        event_type="register",
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip_address=request.client.host if request.client else None,
    )

    await session.commit()
    logger.info("user_registered", user_id=user.id)

    return _map_user(user)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Authenticate a user and issue tokens.

    Rate-limited per client IP (5 attempts per 60s window).
    Returns the SAME generic error for "email not found" and "wrong password"
    to prevent user enumeration (per security plan §1). Response timing is
    equalized too: the "email not found" branch runs a dummy bcrypt check
    (_DUMMY_PASSWORD_HASH) before returning, so it costs the same as the
    real verify_password() call on the "wrong password" branch — without
    this, the two branches were distinguishable by latency alone even
    though their error bodies were already identical.
    """
    # Rate limit
    client_key = get_client_key(request)
    if not check_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait and try again.",
        )

    result = await session.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()

    # Generic error — same message regardless of whether the email exists
    generic_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
    )

    if user is None:
        AUTH_FAILURE_COUNTER.labels(reason="unknown_email").inc()
        verify_password(body.password, _DUMMY_PASSWORD_HASH)  # equalize timing; result unused
        raise generic_error

    # Verify password — bcrypt does constant-time comparison internally
    if not verify_password(body.password, user.password_hash):
        AUTH_FAILURE_COUNTER.labels(reason="wrong_password").inc()
        await _create_audit_event(
            session=session,
            event_type="login_failed",
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            ip_address=request.client.host if request.client else None,
        )
        # Still commit the audit event even on failed login
        await session.commit()
        raise generic_error

    if user.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is not active.",
        )

    # Issue tokens
    access_token = create_access_token(user.id)
    refresh_token = generate_refresh_token()

    # Store session
    session_row = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        access_token_hash=hash_token(access_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    session.add(session_row)

    # Update last_active
    user.last_active = datetime.now(timezone.utc)

    await _create_audit_event(
        session=session,
        event_type="login",
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    await session.commit()
    logger.info("user_logged_in", user_id=user.id)

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_map_user(user),
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Redeem a refresh token for a fresh access token.

    Closes the gap that made an expired access token unrecoverable: the
    30-day refresh token issued at login was stored in user_sessions but
    nothing redeemed it, so the only way past a rejected access token was
    for the user to log in again (frontend api.ts cleared local auth on
    any 401).

    Deliberately NOT authenticated via get_current_user — the whole point
    is to be reachable when the access token is expired or otherwise no
    longer accepted. Authorization comes from the refresh token itself,
    which is a 512-bit secret stored only as a hash, and every check
    get_current_user applies to a session is re-applied here (live,
    non-revoked, unexpired row; active user).

    The refresh token is intentionally NOT rotated. Rotation is the
    stronger posture in general, but with one row per session it makes
    concurrent redemptions (two tabs recovering from the same expired
    token) invalidate each other, reintroducing exactly the spurious
    logout this endpoint exists to remove. The token stays revocable via
    /auth/logout and bounded by the session's absolute expires_at, which
    is not extended here — refreshing renews access, it does not
    lengthen the session.
    """
    # Rate limited under its own key namespace ("refresh:<ip>") rather
    # than the bare client key used by login/register. The limiter blocks
    # a violating key for 5 minutes, and this endpoint is called
    # automatically by the client rather than by a person — sharing the
    # key would let a burst of background refreshes lock the same IP out
    # of /auth/login, turning a recoverable session into a hard lockout.
    client_key = get_client_key(request)
    if not check_rate_limit(f"refresh:{client_key}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many refresh attempts. Please wait and try again.",
        )

    # One error for every failure mode below: which specific check failed
    # (unknown token vs revoked vs expired vs suspended account) is not
    # something an unauthenticated caller should be able to distinguish.
    invalid_token = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token is invalid, expired, or has been revoked.",
    )

    result = await session.execute(
        select(UserSession).where(
            UserSession.refresh_token_hash == hash_token(body.refresh_token),
            UserSession.revoked_at.is_(None),
        )
    )
    user_session = result.scalar_one_or_none()

    if user_session is None:
        AUTH_FAILURE_COUNTER.labels(reason="invalid_refresh_token").inc()
        raise invalid_token

    if user_session.expires_at <= datetime.now(timezone.utc):
        AUTH_FAILURE_COUNTER.labels(reason="expired_refresh_token").inc()
        raise invalid_token

    user_result = await session.execute(
        select(User).where(User.id == user_session.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None or user.status != "active":
        AUTH_FAILURE_COUNTER.labels(reason="inactive_user").inc()
        raise invalid_token

    # Replaces the hash the old access token was looked up by, so the
    # superseded token stops authenticating immediately instead of
    # remaining valid alongside the new one until its own JWT exp.
    access_token = create_access_token(user.id)
    user_session.access_token_hash = hash_token(access_token)
    user.last_active = datetime.now(timezone.utc)

    await _create_audit_event(
        session=session,
        event_type="token_refreshed",
        user_id=user.id,
        entity_type="user_session",
        entity_id=user_session.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    await session.commit()
    logger.info("access_token_refreshed", user_id=user.id, session_id=user_session.id)

    return LoginResponse(
        access_token=access_token,
        refresh_token=body.refresh_token,
        user=_map_user(user),
    )


@router.post("/logout", status_code=204)
async def logout(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """End the current session. Revokes all refresh tokens for this user.

    Per security plan §1: invalidate all sessions, not just client-side discard.
    """
    # Revoke all active sessions for this user
    result = await session.execute(
        select(UserSession).where(
            UserSession.user_id == current_user.id,
            UserSession.revoked_at.is_(None),
        )
    )
    sessions = result.scalars().all()
    for sess in sessions:
        sess.revoked_at = datetime.now(timezone.utc)

    await _create_audit_event(
        session=session,
        event_type="logout",
        user_id=current_user.id,
        entity_type="user",
        entity_id=current_user.id,
    )

    await session.commit()
    logger.info("user_logged_out", user_id=current_user.id)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    """Return the current user's profile and permissions."""
    return _map_user(current_user)


@router.post("/claim-trial", response_model=ClaimTrialResponse)
async def claim_trial(
    body: ClaimTrialRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Attach an anonymous trial session's data to the just-registered/
    logged-in account (Sprint 2). Call immediately after register/login
    when the frontend is carrying a trial session id.

    Single transaction: every cv_files/cv_profile_versions/job_posts/
    match_runs/processing_jobs row still pointing at the trial session is
    reassigned to the new user_id (trial_session_id cleared) and the
    trial_session itself is marked claimed, all before the one commit at
    the end — if anything here raises, nothing is persisted and no row
    is left half-reassigned or orphaned.

    404 if the trial session doesn't exist; 409 if it's expired or was
    already claimed (by this or another account) — claiming is a one-time
    transition, not idempotent, so a second call is a conflict, not a
    no-op.
    """
    result = await claim_trial_session(session, body.trial_session_id, current_user.id)

    await _create_audit_event(
        session=session,
        event_type="trial_claimed",
        user_id=current_user.id,
        entity_type="trial_session",
        entity_id=body.trial_session_id,
        ip_address=request.client.host if request.client else None,
    )

    await session.commit()

    logger.info(
        "trial_session_claimed",
        trial_session_id=body.trial_session_id,
        user_id=current_user.id,
        cv_files=result.cv_files_reassigned,
        job_posts=result.job_posts_reassigned,
        match_runs=result.match_runs_reassigned,
    )

    return ClaimTrialResponse(
        claimed=True,
        cv_files_reassigned=result.cv_files_reassigned,
        job_posts_reassigned=result.job_posts_reassigned,
        match_runs_reassigned=result.match_runs_reassigned,
    )