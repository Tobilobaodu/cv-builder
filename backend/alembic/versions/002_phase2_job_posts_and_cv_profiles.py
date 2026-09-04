"""Phase 2 — Job post ingestion and CV profile child tables.

Creates:
  - job_posts
  - job_post_profiles
  - cv_experience_items
  - cv_education_items
  - cv_skill_items

Adds:
  - master_profile_id (nullable) to cv_profile_versions

Revision ID: 002_phase2_job_posts_and_cv_profiles
Revises: 001_initial_phase1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── job_posts ────────────────────────────────────────────────────
    op.create_table(
        "job_posts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), default="pending", nullable=False,
                  index=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_job_posts_user", "job_posts", ["user_id"])

    # ── job_post_profiles ────────────────────────────────────────────
    op.create_table(
        "job_post_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("job_post_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("job_posts.id"), unique=True, nullable=False),
        sa.Column("job_title", sa.String(255), nullable=True),
        sa.Column("employer", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("required_skills", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("preferred_skills", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("responsibilities", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("qualifications", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("keywords", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("seniority", sa.String(50), nullable=True),
        sa.Column("structured_json", postgresql.JSONB(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_job_profiles_job", "job_post_profiles",
                    ["job_post_id"], unique=True)

    # ── cv_experience_items ──────────────────────────────────────────
    op.create_table(
        "cv_experience_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_profile_version_id",
                  postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"),
                  nullable=False, index=True),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current", sa.Boolean(), default=False),
        sa.Column("bullets", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("technologies", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )

    # ── cv_education_items ───────────────────────────────────────────
    op.create_table(
        "cv_education_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_profile_version_id",
                  postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"),
                  nullable=False, index=True),
        sa.Column("institution", sa.String(255), nullable=True),
        sa.Column("degree", sa.String(255), nullable=True),
        sa.Column("field", sa.String(255), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )

    # ── cv_skill_items ───────────────────────────────────────────────
    op.create_table(
        "cv_skill_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_profile_version_id",
                  postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"),
                  nullable=False, index=True),
        sa.Column("skill_name", sa.String(255), nullable=False, index=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )

    # ── cv_profile_versions: master CV lineage (extension #4) ────────
    op.add_column(
        "cv_profile_versions",
        sa.Column("master_profile_id",
                  postgresql.UUID(as_uuid=False),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cv_profile_versions", "master_profile_id")
    op.drop_table("cv_skill_items")
    op.drop_table("cv_education_items")
    op.drop_table("cv_experience_items")
    op.drop_index("idx_job_profiles_job", table_name="job_post_profiles")
    op.drop_index("idx_job_posts_user", table_name="job_posts")
    op.drop_table("job_post_profiles")
    op.drop_table("job_posts")