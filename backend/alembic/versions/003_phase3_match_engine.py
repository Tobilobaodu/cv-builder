"""Phase 3 — Match engine tables.

Creates:
  - match_runs
  - match_evidence_items

Revision ID: 003
Revises: 002
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "match_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("cv_profile_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"), nullable=False, index=True),
        sa.Column("job_post_profile_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("job_post_profiles.id"), nullable=False, index=True),
        sa.Column("status", sa.String(50), default="pending", nullable=False,
                  index=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("supported_count", sa.Integer(), nullable=True),
        sa.Column("partial_count", sa.Integer(), nullable=True),
        sa.Column("unsupported_count", sa.Integer(), nullable=True),
        sa.Column("total_requirements", sa.Integer(), nullable=True),
        sa.Column("summary_analysis", sa.Text(), nullable=True),
        sa.Column("match_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "match_evidence_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("match_run_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("match_runs.id"), nullable=False, index=True),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("requirement_type", sa.String(20), nullable=False),
        sa.Column("support_level", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_references", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=True),
        sa.Column("warning", sa.Text(), nullable=True),
        sa.Column("user_feedback", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("match_evidence_items")
    op.drop_table("match_runs")