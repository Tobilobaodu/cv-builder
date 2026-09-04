"""Phase 4 — Cover letter workflow tables.

Creates:
  - cover_letter_workflows
  - cover_letter_questions
  - cover_letter_answers
  - cover_letter_drafts

Revision ID: 005
Revises: 004
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cover_letter_workflows",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("cv_profile_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"), nullable=False, index=True),
        sa.Column("job_post_profile_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("job_post_profiles.id"), nullable=False, index=True),
        sa.Column("match_run_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("match_runs.id"), nullable=True),
        sa.Column("status", sa.String(50), default="awaiting_answers",
                  nullable=False, index=True),
        sa.Column("current_step", sa.Integer(), default=1),
        sa.Column("total_steps", sa.Integer(), default=3),
        sa.Column("question_set_version", sa.Integer(), default=1),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "cover_letter_questions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cover_letter_workflows.id"), nullable=False, index=True),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_category", sa.String(50), nullable=False),
        sa.Column("required", sa.Boolean(), default=False),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column("source_evidence_item_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("match_evidence_items.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "cover_letter_answers",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cover_letter_workflows.id"), nullable=False, index=True),
        sa.Column("question_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cover_letter_questions.id"), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "cover_letter_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cover_letter_workflows.id"), nullable=False, index=True),
        sa.Column("version_number", sa.Integer(), default=1),
        sa.Column("status", sa.String(50), default="generated", nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("evidence_references", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("tone", sa.String(50), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("cover_letter_drafts")
    op.drop_table("cover_letter_answers")
    op.drop_table("cover_letter_questions")
    op.drop_table("cover_letter_workflows")