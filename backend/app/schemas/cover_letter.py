"""Pydantic schemas for cover letter workflow — matching 05-openapi.yaml."""

from datetime import datetime
from pydantic import BaseModel, Field


class StartWorkflowRequest(BaseModel):
    cvId: str
    jobPostId: str
    matchId: str | None = None

    model_config = {"populate_by_name": True}


class CoverLetterWorkflowResponse(BaseModel):
    id: str
    cv_id: str = Field(alias="cvId")
    job_post_id: str = Field(alias="jobPostId")
    match_id: str | None = Field(None, alias="matchId")
    current_step: int = Field(alias="currentStep")
    status: str
    question_set_version: int = Field(alias="questionSetVersion")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class CoverLetterWorkflowListItem(BaseModel):
    id: str
    job_post_id: str = Field(alias="jobPostId")
    job_title: str | None = Field(None, alias="jobTitle")
    employer: str | None = None
    status: str
    current_step: int = Field(alias="currentStep")
    total_steps: int = Field(alias="totalSteps")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class CoverLetterWorkflowListResponse(BaseModel):
    items: list[CoverLetterWorkflowListItem]
    total: int
    limit: int
    offset: int


class CoverLetterQuestionResponse(BaseModel):
    id: str
    step_number: int = Field(alias="stepNumber")
    question_text: str = Field(alias="questionText")
    question_category: str = Field(alias="questionCategory")

    model_config = {"from_attributes": True, "populate_by_name": True}


class AnswerItem(BaseModel):
    questionId: str
    answerText: str


class SubmitAnswersRequest(BaseModel):
    answers: list[AnswerItem]


class CoverLetterDraftResponse(BaseModel):
    id: str
    workflow_id: str = Field(alias="workflowId")
    version_number: int = Field(alias="versionNumber")
    status: str
    body_text: str = Field(alias="bodyText")
    evidence_references: list[str] | None = Field(None, alias="evidenceReferences")
    prompt_version: str | None = Field(None, alias="promptVersion")
    model_id: str | None = Field(None, alias="modelId")
    created_at: str = Field(alias="createdAt")
    approved_at: str | None = Field(None, alias="approvedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}