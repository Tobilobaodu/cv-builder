"""Pydantic schemas for the free-API job feed (item 7)."""

from datetime import datetime

from pydantic import BaseModel, Field


class FeedJobPostingOut(BaseModel):
    id: str
    source: str
    title: str
    company: str | None = None
    location: str | None = None
    remote: bool | None = None
    url: str
    description: str
    tags: list[str] | None = None
    salary_text: str | None = Field(None, alias="salaryText")
    posted_at: datetime | None = Field(None, alias="postedAt")
    fetched_at: datetime = Field(alias="fetchedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class FeedJobPostingListResponse(BaseModel):
    items: list[FeedJobPostingOut]
    total: int
    limit: int
    offset: int


class FeedImportAccepted(BaseModel):
    jobPostId: str
    processingJobId: str
