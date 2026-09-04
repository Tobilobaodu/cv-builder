"""Pydantic schemas for Sprint 5 exports — matching 05-openapi.yaml
(extended, see Implementation pack/05-openapi.yaml Sprint 5 additions).

Field-name/alias convention: snake_case field name, camelCase alias —
per schemas/tailored_cv.py's already-correct convention. Worth stating
explicitly here given this exact bug, in the opposite direction, has hit
this codebase three separate times this session (schemas/cover_letter.py,
job_posts.py, matches.py — camelCase field name + snake_case alias,
which silently serializes wrong despite FastAPI's response_model_by_alias
default of True).
"""

from datetime import datetime

from pydantic import BaseModel, Field


class CreateExportRequest(BaseModel):
    template_id: str | None = Field(None, alias="templateId")

    model_config = {"populate_by_name": True}


class ApplicationPackExportRequest(BaseModel):
    tailored_cv_draft_id: str = Field(alias="tailoredCvDraftId")
    cover_letter_workflow_id: str = Field(alias="coverLetterWorkflowId")
    template_id: str | None = Field(None, alias="templateId")

    model_config = {"populate_by_name": True}


class ExportRequestOut(BaseModel):
    id: str
    status: str  # queued, processing, completed, failed (pending maps to queued)
    source_draft_type: str = Field(alias="sourceDraftType")  # tailored_cv, cover_letter, application_pack
    file_reference: str | None = Field(None, alias="fileReference")
    format: str
    template_id: str | None = Field(None, alias="templateId")
    downloaded_at: datetime | None = Field(None, alias="downloadedAt")
    derived_from_export_id: str | None = Field(None, alias="derivedFromExportId")
    error_message: str | None = Field(None, alias="errorMessage")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class ExportTemplateOut(BaseModel):
    id: str
    name: str
    description: str

    model_config = {"populate_by_name": True}
