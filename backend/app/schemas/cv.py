"""Pydantic schemas for CV upload and processing — matching 05-openapi.yaml."""

from datetime import datetime
from pydantic import BaseModel, Field


class CvUploadAccepted(BaseModel):
    cv_id: str = Field(alias="cvId")
    processing_job_id: str = Field(alias="processingJobId")
    status: str  # queued
    filename: str
    file_size: int = Field(alias="fileSize")
    mime_type: str = Field(alias="mimeType")

    model_config = {"populate_by_name": True}


class CvFileResponse(BaseModel):
    id: str
    original_filename: str = Field(alias="originalFilename")
    mime_type: str = Field(alias="mimeType")
    file_size_bytes: int = Field(alias="fileSizeBytes")
    status: str  # Derived lifecycle status — the single value frontends should display
    upload_status: str = Field(alias="uploadStatus")
    processing_status: str = Field(alias="processingStatus")
    job_status: str | None = Field(None, alias="jobStatus")
    # From the latest CvAnalysis row for this CV, if the cv_analyze job has
    # completed — None while analysis hasn't run yet or is still in
    # progress, same "surface what's ready, don't block on it" precedent
    # as job_status above.
    resume_score: float | None = Field(None, alias="resumeScore")
    issue_count: int | None = Field(None, alias="issueCount")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class CvListResponse(BaseModel):
    items: list[CvFileResponse]
    total: int
    limit: int
    offset: int


class CvExtractionPassResponse(BaseModel):
    id: str
    pass_type: str = Field(alias="passType")
    attempt_number: int = Field(alias="attemptNumber")
    confidence_score: float | None = Field(alias="confidenceScore", default=None)
    processing_duration_ms: int | None = Field(alias="processingDurationMs", default=None)
    created_at: datetime = Field(alias="createdAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class StructuralValidationResult(BaseModel):
    section_count_match: bool = Field(alias="sectionCountMatch")
    heading_alignment_score: float = Field(alias="headingAlignmentScore")
    reading_order_consistent: bool = Field(alias="readingOrderConsistent")
    date_range_consistent: bool = Field(alias="dateRangeConsistent")
    bullet_preservation_score: float = Field(alias="bulletPreservationScore")
    anomaly_detected: bool = Field(alias="anomalyDetected")
    anomaly_detail: str | None = Field(alias="anomalyDetail")

    model_config = {"populate_by_name": True}


class CvExtractionDetailResponse(BaseModel):
    passes: list[CvExtractionPassResponse]
    structural_validation: StructuralValidationResult | None = Field(
        alias="structuralValidation", default=None
    )

    model_config = {"populate_by_name": True}


class CvRawTextResponse(BaseModel):
    canonical_text: str = Field(alias="canonicalText")
    ocr_used: bool = Field(alias="ocrUsed")
    merge_strategy_metadata: dict | None = Field(alias="mergeStrategyMetadata")

    model_config = {"from_attributes": True, "populate_by_name": True}




class AtsCheckItem(BaseModel):
    check_type: str = Field(alias="checkType")
    passed: bool
    severity: str
    detail: str

    model_config = {"populate_by_name": True}


class AtsReadinessCheckResponse(BaseModel):
    id: str
    cv_id: str = Field(alias="cvId")
    cv_profile_version_id: str | None = Field(None, alias="cvProfileVersionId")
    overall_score: float = Field(alias="overallScore")
    contact_info_parseable: bool | None = Field(None, alias="contactInfoParseable")
    checks: list[AtsCheckItem]
    created_at: datetime = Field(alias="createdAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


# ──────────────────────────────────────────────────────────────────────
# LLM-based CV analysis
# ──────────────────────────────────────────────────────────────────────


class CvIssueItem(BaseModel):
    """One ATS or formatting checklist entry from a CvAnalysis row.

    Distinct shape from AtsCheckItem above (no `check_type`, has `title`)
    — matches the LLM engine's own atsIssues/formattingIssues contract
    (see app/prompts/cv_analysis_prompts.py), not the older rules-based
    ats_check.py's checklist shape.
    """

    passed: bool
    severity: str
    title: str
    detail: str

    model_config = {"populate_by_name": True}


class CvAnalysisResponse(BaseModel):
    id: str
    cv_id: str = Field(alias="cvId")
    cv_profile_version_id: str | None = Field(None, alias="cvProfileVersionId")
    overall_score: float = Field(alias="overallScore")
    skillset_score: float = Field(alias="skillsetScore")
    formatting_score: float = Field(alias="formattingScore")
    ats_issues: list[CvIssueItem] = Field(alias="atsIssues")
    formatting_issues: list[CvIssueItem] = Field(alias="formattingIssues")
    tips: list[str]
    created_at: datetime = Field(alias="createdAt")

    model_config = {"from_attributes": True, "populate_by_name": True}

