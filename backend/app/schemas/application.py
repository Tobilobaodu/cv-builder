"""Pydantic schemas for application / response-rate tracking (D5, 6a/6b).

Field-name/alias convention: snake_case field name, camelCase alias —
per schemas/tailored_cv.py's/schemas/coverage.py's convention.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class CreateApplicationRequest(BaseModel):
    job_post_id: str | None = Field(None, alias="jobPostId")
    tailored_cv_draft_id: str | None = Field(None, alias="tailoredCvDraftId")
    cover_letter_draft_id: str | None = Field(None, alias="coverLetterDraftId")
    # Required even when job_post_id is set — job_post's own profile may
    # not have finished structuring yet (or may have none at all), and an
    # application record should never be blocked on that.
    job_title: str = Field(..., alias="jobTitle", min_length=1, max_length=255)
    employer: str = Field(..., alias="employer", min_length=1, max_length=255)
    applied_at: datetime | None = Field(None, alias="appliedAt")
    notes: str | None = Field(None, max_length=5000)

    model_config = {"populate_by_name": True}


class UpdateApplicationStatusRequest(BaseModel):
    status: str
    note: str | None = Field(None, max_length=5000)

    model_config = {"populate_by_name": True}


class AddApplicationNoteRequest(BaseModel):
    note: str = Field(..., min_length=1, max_length=5000)

    model_config = {"populate_by_name": True}


class ApplicationEventOut(BaseModel):
    id: str
    event_type: str = Field(alias="eventType")
    from_status: str | None = Field(None, alias="fromStatus")
    to_status: str | None = Field(None, alias="toStatus")
    note: str | None = None
    actor_type: str = Field(alias="actorType")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class ApplicationOut(BaseModel):
    id: str
    job_post_id: str | None = Field(None, alias="jobPostId")
    tailored_cv_draft_id: str | None = Field(None, alias="tailoredCvDraftId")
    cover_letter_draft_id: str | None = Field(None, alias="coverLetterDraftId")
    job_title: str = Field(alias="jobTitle")
    employer: str
    status: str
    applied_at: datetime = Field(alias="appliedAt")
    notes: str | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    events: list[ApplicationEventOut] = Field(default_factory=list)

    model_config = {"from_attributes": True, "populate_by_name": True}


class ApplicationListItem(BaseModel):
    id: str
    job_post_id: str | None = Field(None, alias="jobPostId")
    job_title: str = Field(alias="jobTitle")
    employer: str
    status: str
    applied_at: datetime = Field(alias="appliedAt")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class ApplicationListResponse(BaseModel):
    items: list[ApplicationListItem]
    total: int
    limit: int
    offset: int


class ApplicationStatsOut(BaseModel):
    total: int
    by_status: dict[str, int] = Field(alias="byStatus")
    # Fraction (0-1) of applications that have left the initial "applied"
    # state for any reason other than the applicant withdrawing it
    # themselves — see application_states.RESPONDED_STATUSES. None when
    # there are zero applications (nothing to divide by, not zero signal).
    response_rate: float | None = Field(None, alias="responseRate")

    model_config = {"populate_by_name": True}
