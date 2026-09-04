"""LLM-based CV analysis — replaces the decommissioned structured-parsing
pipeline's role as the source of a per-CV quality signal.

Creates:
  - cv_analyses

Revision ID: 015
Revises: 014
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cv_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "cv_file_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("cv_files.id"), nullable=False,
        ),
        sa.Column(
            "cv_profile_version_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("cv_profile_versions.id"), nullable=True,
        ),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("skillset_score", sa.Float(), nullable=False),
        sa.Column("formatting_score", sa.Float(), nullable=False),
        sa.Column("ats_issues", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("formatting_issues", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("tips", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("idx_cv_analyses_cv", "cv_analyses", ["cv_file_id"])
    op.create_index(
        "idx_cv_analyses_profile", "cv_analyses", ["cv_profile_version_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_cv_analyses_profile", table_name="cv_analyses")
    op.drop_index("idx_cv_analyses_cv", table_name="cv_analyses")
    op.drop_table("cv_analyses")
