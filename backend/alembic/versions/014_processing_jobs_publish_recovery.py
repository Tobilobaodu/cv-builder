"""Add processing_jobs publish/recovery bookkeeping

A processing job can be orphaned between the API producer and the
Celery/Redis broker: send_task() returns without error but the message
never reaches a worker (transient broker blip, connection-pool exhaustion,
a worker that never picks it up). Such a job sits at pending/queued with
no started_at forever, indistinguishable from a slow-but-progressing one.

These columns let a scheduled recovery task find and republish those jobs:
published_at/celery_task_id record the most recent republish, and
publish_attempts bounds how many times recovery retries before giving up.

Revision ID: 014
Revises: 013
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "processing_jobs",
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "processing_jobs",
        sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "processing_jobs",
        sa.Column("last_publish_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "last_publish_error")
    op.drop_column("processing_jobs", "publish_attempts")
    op.drop_column("processing_jobs", "celery_task_id")
    op.drop_column("processing_jobs", "published_at")