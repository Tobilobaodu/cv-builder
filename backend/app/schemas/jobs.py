"""Pydantic schemas for processing jobs — matching 05-openapi.yaml."""

from datetime import datetime
from pydantic import BaseModel, Field


class ProcessingJobResponse(BaseModel):
    id: str
    job_type: str = Field(alias="jobType")
    source_entity_type: str = Field(alias="sourceEntityType")
    source_entity_id: str = Field(alias="sourceEntityId")
    status: str  # queued, processing, completed, failed, retrying
    retry_count: int = Field(alias="retryCount")
    last_error: str | None = Field(alias="lastError", default=None)
    created_at: datetime = Field(alias="createdAt")
    # started_at is surfaced so callers can tell "queued/never picked up"
    # (started_at None) apart from "slowly processing" (started_at set) — a
    # job that looks stuck at a bare "pending"/"queued" for minutes is the
    # first case, and this field is the only way to distinguish the two
    # without a direct DB query.
    started_at: datetime | None = Field(alias="startedAt", default=None)
    completed_at: datetime | None = Field(alias="completedAt", default=None)

    model_config = {"from_attributes": True, "populate_by_name": True}


class ProcessingJobRef(BaseModel):
    job_id: str = Field(alias="jobId")
    status: str  # queued

    model_config = {"populate_by_name": True}