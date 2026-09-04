"""Pydantic schemas for tailored CV generation — matching 05-openapi.yaml.

Field-name/alias convention: snake_case field name, camelCase alias —
verified directly against FastAPI's actual serialization behavior (not
assumed): with response_model_by_alias defaulting to True, a
snake_case-field/camelCase-alias schema serializes to camelCase JSON,
while the inverse (camelCase field/snake_case alias, used elsewhere in
this codebase in schemas/cover_letter.py) silently serializes to
snake_case instead. Follows schemas/cv.py's already-correct convention,
not cover_letter.py's.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class TailoredCvSectionResponse(BaseModel):
    id: str
    section_type: str = Field(alias="sectionType")
    content_text: str = Field(alias="contentText")
    evidence_references: list[str] = Field(alias="evidenceReferences")
    generation_task: str | None = Field(None, alias="generationTask")
    prompt_version: str | None = Field(None, alias="promptVersion")
    model_id: str | None = Field(None, alias="modelId")
    validation_status: str | None = Field(None, alias="validationStatus")
    order_index: int | None = Field(None, alias="orderIndex")

    model_config = {"from_attributes": True, "populate_by_name": True}


class ValidationResultResponse(BaseModel):
    passed: bool
    issues: list[str]

    model_config = {"populate_by_name": True}


class TailoredCvDraftResponse(BaseModel):
    id: str
    match_run_id: str = Field(alias="matchRunId")
    version_number: int = Field(alias="versionNumber")
    status: str
    sections: list[TailoredCvSectionResponse]
    validation_result: ValidationResultResponse | None = Field(None, alias="validationResult")
    improvement_checklist: list[dict] | None = Field(None, alias="improvementChecklist")
    created_at: datetime = Field(alias="createdAt")
    approved_at: datetime | None = Field(None, alias="approvedAt")

    model_config = {"from_attributes": True, "populate_by_name": True}


class RegenerateRequest(BaseModel):
    instructions: str | None = None

    model_config = {"populate_by_name": True}
