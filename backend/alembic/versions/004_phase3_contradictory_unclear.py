"""Phase 3 — Add contradictory_count and unclear_count to match_runs.

Revision ID: 004
Revises: 003
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "match_runs",
        sa.Column("contradictory_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "match_runs",
        sa.Column("unclear_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("match_runs", "unclear_count")
    op.drop_column("match_runs", "contradictory_count")