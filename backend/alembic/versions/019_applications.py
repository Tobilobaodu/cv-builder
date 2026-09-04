"""Applications / response-rate tracking (D5, 6a/6b).

Creates `applications` (account-only, user_id NOT NULL — same shape as
cover_letter_workflows/job_post_collections/coverage_reports) and the
paired append-only `application_events` log.

RLS: `applications` gets the same ENABLE-only treatment migration 018
gave the other account-only tables (018's _ACCOUNT_ONLY_TABLES list
predates this table's existence, so it's applied here instead of by
editing that migration). `application_events` is deliberately NOT
RLS-covered — same precedent as match_evidence_items/
tailored_cv_sections/cover_letter_questions: a child table reached only
through a join from its already-covered parent.

Revision ID: 019
Revises: 018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GUC_EXPR = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_post_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("job_posts.id"), nullable=True),
        sa.Column("tailored_cv_draft_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("tailored_cv_drafts.id"), nullable=True),
        sa.Column("cover_letter_draft_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("cover_letter_drafts.id"), nullable=True),
        sa.Column("job_title", sa.String(255), nullable=False),
        sa.Column("employer", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="applied"),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_job_post_id", "applications", ["job_post_id"])
    op.create_index("ix_applications_status", "applications", ["status"])

    op.create_table(
        "application_events",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_application_events_application_id", "application_events", ["application_id"])

    bind = op.get_bind()
    bind.execute(sa.text("ALTER TABLE applications ENABLE ROW LEVEL SECURITY"))
    bind.execute(
        sa.text(
            "CREATE POLICY applications_owner_isolation ON applications "
            f"USING (user_id = {_GUC_EXPR})"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DROP POLICY IF EXISTS applications_owner_isolation ON applications"))
    bind.execute(sa.text("ALTER TABLE applications DISABLE ROW LEVEL SECURITY"))
    op.drop_table("application_events")
    op.drop_table("applications")
