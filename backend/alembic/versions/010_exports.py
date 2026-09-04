"""Sprint 5 — Exports.

Creates:
  - exports (nullable user_id + trial_session_id + "exactly one owner"
    CHECK, same pattern as tailored_cv_drafts/cv_files/job_posts/
    match_runs — export_type='cv' must be trial-session-accessible since
    tailored CV generation itself already is end-to-end; cover_letter/
    application_pack rows always have trial_session_id NULL since they
    require a CoverLetterWorkflow, which is account-only. This is a
    deliberate deviation from 03-data-model.md's literal
    `user_id NOT NULL` — same class of documented deviation Sprint 2
    made project-wide.

    format resolves 03-data-model.md's previously-open format decision:
    docx (primary), pdf (secondary, gated behind downloaded_at being
    set), zip (application packs — two independent docx files, not a
    merged document).

Also adds:
  - tailored_cv_sections.source_item_id — nullable, no FK (polymorphic,
    same no-FK pattern processing_jobs.source_entity_id already uses).
    Needed so the DOCX export renderer can put a real company/title/date
    header on each experience/project section instead of a bare bullet
    list — TailoredCvDraft.content_json has one section per role/project
    but nothing today points back to which CvExperienceItem/CvProjectItem
    it came from.

Revision ID: 010
Revises: 009
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tailored_cv_sections",
        sa.Column("source_item_id", postgresql.UUID(as_uuid=False), nullable=True),
    )

    op.create_table(
        "exports",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("trial_session_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("trial_sessions.id"), nullable=True),
        sa.Column("export_type", sa.String(20), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("secondary_source_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("format", sa.String(20), server_default="docx", nullable=False),
        sa.Column("template_id", sa.String(50), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("derived_from_export_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("exports.id"), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_exports_exactly_one_owner",
        "exports",
        "(user_id IS NOT NULL AND trial_session_id IS NULL) OR "
        "(user_id IS NULL AND trial_session_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_exports_trial_only_for_cv",
        "exports",
        "(trial_session_id IS NULL) OR (export_type = 'cv')",
    )
    op.create_check_constraint(
        "ck_exports_format",
        "exports",
        "format IN ('docx', 'pdf', 'zip')",
    )
    op.create_check_constraint(
        "ck_exports_pdf_requires_source",
        "exports",
        "(format != 'pdf') OR (derived_from_export_id IS NOT NULL)",
    )
    op.create_index("idx_exports_user", "exports", ["user_id"])
    op.create_index("idx_exports_trial_session", "exports", ["trial_session_id"])
    op.create_index("idx_exports_status", "exports", ["status"])
    op.create_index("idx_exports_source_id", "exports", ["source_id"])
    op.create_index("idx_exports_derived_from", "exports", ["derived_from_export_id"])


def downgrade() -> None:
    op.drop_index("idx_exports_derived_from", table_name="exports")
    op.drop_index("idx_exports_source_id", table_name="exports")
    op.drop_index("idx_exports_status", table_name="exports")
    op.drop_index("idx_exports_trial_session", table_name="exports")
    op.drop_index("idx_exports_user", table_name="exports")
    op.drop_constraint("ck_exports_pdf_requires_source", "exports", type_="check")
    op.drop_constraint("ck_exports_format", "exports", type_="check")
    op.drop_constraint("ck_exports_trial_only_for_cv", "exports", type_="check")
    op.drop_constraint("ck_exports_exactly_one_owner", "exports", type_="check")
    op.drop_table("exports")

    op.drop_column("tailored_cv_sections", "source_item_id")
