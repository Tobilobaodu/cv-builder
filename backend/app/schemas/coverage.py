"""Pydantic schemas for multi-job-post coverage reporting — Sprint 5 /
Product Extension #2, matching 05-openapi.yaml.

Field-name/alias convention: snake_case field name, camelCase alias —
per schemas/tailored_cv.py's already-correct convention.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class CreateCollectionRequest(BaseModel):
    name: str
    job_post_ids: list[str] = Field(alias="jobPostIds", min_length=1, max_length=50)

    model_config = {"populate_by_name": True}


class JobPostCollectionOut(BaseModel):
    id: str
    name: str
    job_post_ids: list[str] = Field(alias="jobPostIds")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class CoverageReportTriggerRequest(BaseModel):
    cv_id: str = Field(alias="cvId")

    model_config = {"populate_by_name": True}


class AggregateGapOut(BaseModel):
    requirement_text_cluster: str = Field(alias="requirementTextCluster")
    recurrence_count: int = Field(alias="recurrenceCount")
    recurrence_ratio: float = Field(alias="recurrenceRatio")
    affected_job_post_ids: list[str] = Field(alias="affectedJobPostIds")
    current_support_level_distribution: dict[str, int] = Field(alias="currentSupportLevelDistribution")

    model_config = {"populate_by_name": True}


class CoverageReportOut(BaseModel):
    id: str
    cv_profile_version_id: str = Field(alias="cvProfileVersionId")
    collection_id: str = Field(alias="collectionId")
    match_run_ids: list[str] = Field(default_factory=list, alias="matchRunIds")
    status: str
    aggregate_gaps: list[AggregateGapOut] = Field(default_factory=list, alias="aggregateGaps")
    skipped_job_post_ids: list[str] | None = Field(None, alias="skippedJobPostIds")
    created_at: datetime = Field(alias="createdAt")
    completed_at: datetime | None = Field(None, alias="completedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}
