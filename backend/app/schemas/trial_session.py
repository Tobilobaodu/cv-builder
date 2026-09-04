"""Pydantic schemas for anonymous trial sessions (Sprint 2)."""

from datetime import datetime
from pydantic import BaseModel, Field


class TrialSessionCreated(BaseModel):
    trial_session_id: str = Field(alias="trialSessionId")
    expires_at: datetime = Field(alias="expiresAt")

    model_config = {"populate_by_name": True}
