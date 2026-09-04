"""Sprint 5 / Product Extension #2 — Multi-job-post coverage reporting.

Creates:
  - job_post_collections (account-only, matches 03-data-model.md's
    schema exactly — job_post_ids as a native Postgres UUID array,
    matching the existing cv_profile_versions.source_pass_ids precedent,
    not a join table)
  - coverage_reports (account-only; a pure read/aggregation over
    existing match_runs/match_evidence_items — no new matching logic.
    skipped_job_post_ids is new beyond the documented schema, recording
    postings skipped for missing/unstructured JobPostProfile data)

Revision ID: 011
Revises: 010
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_post_collections",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("job_post_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)),
                  server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_job_post_collections_user", "job_post_collections", ["user_id"])

    op.create_table(
        "coverage_reports",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cv_profile_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("job_post_collections.id"), nullable=False),
        sa.Column("match_run_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)), nullable=True),
        sa.Column("aggregate_gaps", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("skipped_job_post_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=False)), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_coverage_user", "coverage_reports", ["user_id"])
    op.create_index("idx_coverage_collection", "coverage_reports", ["collection_id"])
    op.create_index("idx_coverage_status", "coverage_reports", ["status"])


def downgrade() -> None:
    op.drop_index("idx_coverage_status", table_name="coverage_reports")
    op.drop_index("idx_coverage_collection", table_name="coverage_reports")
    op.drop_index("idx_coverage_user", table_name="coverage_reports")
    op.drop_table("coverage_reports")

    op.drop_index("idx_job_post_collections_user", table_name="job_post_collections")
    op.drop_table("job_post_collections")
