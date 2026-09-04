"""Product Extension #1 — ATS structural-readiness checks.

Creates:
  - ats_readiness_checks

Revision ID: 007
Revises: 006
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ats_readiness_checks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "cv_file_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("cv_files.id"), nullable=False,
        ),
        sa.Column(
            "cv_profile_version_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("cv_profile_versions.id"), nullable=True,
        ),
        sa.Column("overall_score", sa.Numeric(3, 2), nullable=False),
        sa.Column("checks", postgresql.JSONB(), nullable=False),
        sa.Column("contact_info_parseable", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index("idx_ats_checks_cv", "ats_readiness_checks", ["cv_file_id"])
    op.create_index(
        "idx_ats_checks_profile", "ats_readiness_checks",
        ["cv_profile_version_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_ats_checks_profile", table_name="ats_readiness_checks")
    op.drop_index("idx_ats_checks_cv", table_name="ats_readiness_checks")
    op.drop_table("ats_readiness_checks")
