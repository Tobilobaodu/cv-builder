"""Anonymous trial session endpoint — POST /trial-sessions (Sprint 2).

Creates the identity an anonymous visitor presents on subsequent
trial-eligible requests via the X-Trial-Session-Id header. See
app/core/security.py's get_current_user_or_trial_session for how that
header is consumed, and app/api/v1/auth.py's claim-trial endpoint for
how a trial session is reconciled into a real account.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import check_trial_session_rate_limit, get_client_key
from app.db import get_session
from app.db.models import TrialSession
from app.schemas.trial_session import TrialSessionCreated

router = APIRouter(tags=["trial-sessions"])
logger = get_logger(__name__)


@router.post("/trial-sessions", response_model=TrialSessionCreated, status_code=201)
async def create_trial_session(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Start an anonymous trial. No credentials required — this IS the
    credential-free entry point.

    Rate-limited per client IP (trial_session tier): this is the one
    unauthenticated way to mint an identity, so it's deliberately the
    tightest budget of any tier.
    """
    client_key = get_client_key(request)
    if not check_trial_session_rate_limit(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many trial sessions started from this address. Please wait and try again.",
        )

    now = datetime.now(timezone.utc)
    trial_session = TrialSession(
        expires_at=now + timedelta(hours=settings.trial_session_ttl_hours),
        ip_address=request.client.host if request.client else None,
    )
    session.add(trial_session)
    await session.commit()

    logger.info("trial_session_created", trial_session_id=trial_session.id)

    return TrialSessionCreated(
        trial_session_id=trial_session.id,
        expires_at=trial_session.expires_at,
    )
