"""Soft delete for job posts and match runs — mirrors cv_files.deleted_at
so the Jobs/Reports dashboard pages can offer a delete action the same
way the CVs page does.

Adds:
  - job_posts.deleted_at
  - match_runs.deleted_at

Revision ID: 016
Revises: 015
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_posts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "match_runs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("match_runs", "deleted_at")
    op.drop_column("job_posts", "deleted_at")
