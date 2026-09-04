"""Phase 2 extension — CV certifications and projects.

Creates:
  - cv_certification_items (certifications/diplomas — evaluated together
    with cv_education_items when deciding whether a tailored CV's
    education-type section has any evidence at all; a certification
    without a formal degree still counts)
  - cv_project_items (personal/portfolio projects — deliberately never
    pooled as education evidence; general technical evidence with its
    own dedicated tailored-CV section)

Revision ID: 009
Revises: 008
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cv_certification_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_profile_version_id",
                  postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False, index=True),
        sa.Column("issuer", sa.String(255), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )

    op.create_table(
        "cv_project_items",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("cv_profile_version_id",
                  postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("cv_profile_versions.id"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("technologies", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("bullets", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("cv_project_items")
    op.drop_table("cv_certification_items")
