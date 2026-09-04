"""Free-API job feed catalog (item 7).

`feed_job_postings` is shared inventory ingested by
app/workers/job_feed_jobs.py's periodic refresh task — not a per-user
resource, so (unlike migration 018/019's tables) it gets no user_id
column and no RLS policy, same "not an owned resource" treatment as
users/audit_events.

(source, external_id) is unique — the de-dup key a re-fetch of the same
listing must not duplicate. See app/services/job_feed/ingest.py.

Revision ID: 020
Revises: 019
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feed_job_postings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("salary_text", sa.String(255), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source", "external_id", name="uq_feed_job_postings_source_external_id"),
    )
    op.create_index("ix_feed_job_postings_source", "feed_job_postings", ["source"])
    op.create_index("ix_feed_job_postings_posted_at", "feed_job_postings", ["posted_at"])


def downgrade() -> None:
    op.drop_index("ix_feed_job_postings_posted_at", table_name="feed_job_postings")
    op.drop_index("ix_feed_job_postings_source", table_name="feed_job_postings")
    op.drop_table("feed_job_postings")
