"""POST /client-metrics/journey — client-measured wall-clock timings the
server can't see on its own (poll lag, render time) — see jbs-solution-
sheet.md O4. Without this, S1 through S9 are unfalsifiable and a later
change regressing them would go unnoticed; recorded here rather than
inferred from HTTP_REQUEST_DURATION_SECONDS, which is per-route and blind
to everything between requests.

Metrics-only: nothing here is persisted, so there's no migration and
nothing IDOR-relevant to guard beyond "this identity exists" — the journey
name is a fixed enum, never free text, so a caller can't inflate metric
cardinality.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.metrics import JOURNEY_DURATION_SECONDS
from app.core.security import RequestIdentity, get_current_user_or_trial_session

logger = get_logger(__name__)
router = APIRouter(tags=["client-metrics"])

# Extend as more journeys get instrumented — deliberately not free text,
# so this can never become an arbitrary-label injection point.
_ALLOWED_JOURNEYS = {"cv_upload_to_analysis"}


class JourneyBeacon(BaseModel):
    journey: str
    duration_seconds: float = Field(alias="durationSeconds", gt=0, le=600)

    model_config = {"populate_by_name": True}


@router.post("/client-metrics/journey", status_code=status.HTTP_204_NO_CONTENT)
async def record_journey(
    body: JourneyBeacon,
    identity: RequestIdentity = Depends(get_current_user_or_trial_session),
):
    """Trial-accessible, same as the upload/analysis journey it measures."""
    if body.journey not in _ALLOWED_JOURNEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown journey name.",
        )
    JOURNEY_DURATION_SECONDS.labels(journey=body.journey).observe(body.duration_seconds)
