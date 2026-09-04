"""Sprint 3 — Tailored CV generation.

Creates:
  - tailored_cv_drafts (nullable user_id + trial_session_id + "exactly one
    owner" CHECK constraint from creation, same pattern Sprint 2 applied
    to cv_files/job_posts/match_runs/processing_jobs/cv_profile_versions —
    generation is trial-session-accessible from day one, not retrofitted)
  - tailored_cv_sections (evidence_references non-empty CHECK constraint —
    DB-layer enforcement of the non-fabrication rule, stronger than
    03-data-model.md's app-level-only specification)

Revision ID: 008
Revises: 007
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tailored_cv_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("trial_session_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("trial_sessions.id"), nullable=True),
        sa.Column("match_run_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("match_runs.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("content_json", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("render_text", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("validation_result", postgresql.JSONB(), nullable=True),
        sa.Column("improvement_checklist", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_tailored_cv_drafts_exactly_one_owner",
        "tailored_cv_drafts",
        "(user_id IS NOT NULL AND trial_session_id IS NULL) OR "
        "(user_id IS NULL AND trial_session_id IS NOT NULL)",
    )
    op.create_index("idx_tailored_cv_drafts_user", "tailored_cv_drafts", ["user_id"])
    op.create_index("idx_tailored_cv_drafts_trial_session", "tailored_cv_drafts", ["trial_session_id"])
    op.create_index("idx_tailored_cv_drafts_match_run", "tailored_cv_drafts", ["match_run_id"])
    op.create_index("idx_tailored_cv_drafts_status", "tailored_cv_drafts", ["status"])

    op.create_table(
        "tailored_cv_sections",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("draft_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("tailored_cv_drafts.id"), nullable=False),
        sa.Column("section_type", sa.String(50), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("evidence_references", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("generation_task", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("validation_status", sa.String(20), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_tailored_cv_sections_evidence_nonempty",
        "tailored_cv_sections",
        "cardinality(evidence_references) > 0",
    )
    op.create_index("idx_tailored_cv_sections_draft", "tailored_cv_sections", ["draft_id"])


def downgrade() -> None:
    op.drop_index("idx_tailored_cv_sections_draft", table_name="tailored_cv_sections")
    op.drop_constraint("ck_tailored_cv_sections_evidence_nonempty", "tailored_cv_sections", type_="check")
    op.drop_table("tailored_cv_sections")

    op.drop_index("idx_tailored_cv_drafts_status", table_name="tailored_cv_drafts")
    op.drop_index("idx_tailored_cv_drafts_match_run", table_name="tailored_cv_drafts")
    op.drop_index("idx_tailored_cv_drafts_trial_session", table_name="tailored_cv_drafts")
    op.drop_index("idx_tailored_cv_drafts_user", table_name="tailored_cv_drafts")
    op.drop_constraint("ck_tailored_cv_drafts_exactly_one_owner", "tailored_cv_drafts", type_="check")
    op.drop_table("tailored_cv_drafts")
