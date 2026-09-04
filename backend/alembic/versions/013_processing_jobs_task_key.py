"""Add processing_jobs.task_key for idempotent job creation

A retried client request (network blip, double-click, browser retry) for
the same upload-processing or generation job currently creates a second
ProcessingJob row and a second real worker task (a second docling
extraction, a second LLM generation call) — wasted compute and, for
generation, a second real API-provider spend. task_key is a nullable
sha256 of (job_type, source_entity_id, owner_id), set only by call sites
that opt into dedup; a partial unique index enforces no-two-active-jobs-
for-the-same-key at the database level rather than relying on an
application-level check-then-insert race.

Revision ID: 013
Revises: 012
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("task_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "idx_processing_jobs_task_key_unique",
        "processing_jobs",
        ["task_key"],
        unique=True,
        postgresql_where=sa.text("task_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_processing_jobs_task_key_unique", table_name="processing_jobs")
    op.drop_column("processing_jobs", "task_key")
