"""Initial Phase 1 migration

Creates all core tables for Sprint 1-2: users, user_sessions, cv_files,
cv_extraction_passes, cv_raw_text, cv_profiles, cv_profile_versions,
processing_jobs, audit_events.

Revision ID: 001
Revises: None
Create Date: 2026-08-07
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[Sequence[str], None] = None
depends_on: Union[Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_users_email", "users", ["email"])

    # ── user_sessions ──
    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("access_token", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_sessions_user", "user_sessions", ["user_id"])
    op.create_index("idx_sessions_refresh_token", "user_sessions", ["refresh_token_hash"])
    op.create_index("idx_sessions_expires", "user_sessions", ["expires_at"])

    # ── cv_files ──
    op.create_table(
        "cv_files",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("status", sa.String(50), default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_cvs_user", "cv_files", ["user_id"])
    op.create_index("idx_cvs_status", "cv_files", ["status"])

    # ── cv_extraction_passes ──
    op.create_table(
        "cv_extraction_passes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_file_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("cv_files.id"), nullable=False),
        sa.Column("pass_type", sa.String(50), nullable=False),
        sa.Column("attempt_number", sa.Integer(), default=1, nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("raw_output", postgresql.JSONB(), nullable=True),
        sa.Column("engine", sa.String(100), nullable=True),
        sa.Column("engine_version", sa.String(50), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("characters", sa.Integer(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("processing_duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_passes_cv", "cv_extraction_passes", ["cv_file_id"])
    op.create_unique_constraint(
        "uq_passes_cv_type_attempt",
        "cv_extraction_passes",
        ["cv_file_id", "pass_type", "attempt_number"],
    )

    # ── cv_raw_text ──
    op.create_table(
        "cv_raw_text",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_file_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("cv_files.id"), unique=True, nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("characters", sa.Integer(), nullable=True),
        sa.Column("merge_strategy", sa.String(50), nullable=True),
        sa.Column("merge_strategy_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("ocr_used", sa.Boolean(), default=False),
        sa.Column("structural_validation_result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── cv_profile_versions (must exist before cv_profiles FK) ──
    op.create_table(
        "cv_profile_versions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_file_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("cv_files.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("source_pass_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)), nullable=True),
        sa.Column("structured_payload", postgresql.JSONB(), nullable=False),
        sa.Column("confidence_summary", postgresql.JSONB(), nullable=True),
        sa.Column("validation_status", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_profile_versions_cv", "cv_profile_versions", ["cv_file_id"])
    op.create_index("idx_profile_versions_hash", "cv_profile_versions", ["profile_hash"])

    # ── cv_profiles (pointer) ──
    op.create_table(
        "cv_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_file_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("cv_files.id"), unique=True, nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("cv_profile_versions.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── processing_jobs ──
    op.create_table(
        "processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("source_entity_type", sa.String(50), nullable=False),
        sa.Column("source_entity_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(20), default="pending", nullable=False),
        sa.Column("progress", sa.Float(), default=0, nullable=True),
        sa.Column("retry_count", sa.Integer(), default=0),
        sa.Column("max_retries", sa.Integer(), default=3),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("worker_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_jobs_user", "processing_jobs", ["user_id"])
    op.create_index("idx_jobs_status", "processing_jobs", ["status"])
    op.create_index("idx_jobs_source", "processing_jobs", ["source_entity_type", "source_entity_id"])
    op.create_index("idx_jobs_type", "processing_jobs", ["job_type"])

    # ── audit_events (append-only, no update/delete path) ──
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("actor_type", sa.String(50), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_audit_user", "audit_events", ["user_id"])
    op.create_index("idx_audit_entity", "audit_events", ["entity_type", "entity_id"])
    op.create_index("idx_audit_event", "audit_events", ["event_type"])
    op.create_index("idx_audit_created", "audit_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("processing_jobs")
    op.drop_table("cv_profiles")
    op.drop_table("cv_profile_versions")
    op.drop_table("cv_raw_text")
    op.drop_table("cv_extraction_passes")
    op.drop_table("cv_files")
    op.drop_table("user_sessions")
    op.drop_table("users")