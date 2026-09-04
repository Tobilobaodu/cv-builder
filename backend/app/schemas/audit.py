"""Pydantic schemas for the audit trail endpoint — matching 05-openapi.yaml
GET /audit/{entityType}/{entityId} (AuditEvent schema).

Field-name/alias convention: snake_case field name, camelCase alias — per
schemas/tailored_cv.py's already-correct convention. This exact bug (camelCase
field + snake_case alias silently serializing wrong despite FastAPI's
response_model_by_alias default of True) has hit this codebase three separate
times (schemas/cover_letter.py, job_posts.py, matches.py), so the convention is
stated explicitly here rather than assumed.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AuditEventResponse(BaseModel):
    id: str
    entity_type: str = Field(alias="entityType")
    entity_id: str | None = Field(None, alias="entityId")
    event_type: str = Field(alias="eventType")
    actor_type: str = Field(alias="actorType")
    metadata: dict | None = None
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True}
