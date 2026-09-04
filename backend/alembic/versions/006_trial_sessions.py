"""Sprint 2 — Anonymous trial support.

Creates:
  - trial_sessions

Alters (nullable user_id + new trial_session_id FK + "exactly one owner"
CHECK constraint):
  - cv_files
  - cv_profile_versions (mirrors cv_files — see model docstring: a profile
    version is copied from its cv_file at parse time and must carry the
    same owner)
  - job_posts
  - match_runs
  - processing_jobs (user_id was already nullable; only trial_session_id
    and the CHECK constraint are new here)

Revision ID: 006
Revises: 005
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OWNER_CHECK_SQL = (
    "(user_id IS NOT NULL AND trial_session_id IS NULL) OR "
    "(user_id IS NULL AND trial_session_id IS NOT NULL)"
)

# (table, constraint_name, index_name)
_TRIAL_ELIGIBLE_TABLES = [
    ("cv_files", "ck_cv_files_exactly_one_owner", "idx_cv_files_trial_session"),
    ("cv_profile_versions", "ck_cv_profile_versions_exactly_one_owner", "idx_cv_profile_versions_trial_session"),
    ("job_posts", "ck_job_posts_exactly_one_owner", "idx_job_posts_trial_session"),
    ("match_runs", "ck_match_runs_exactly_one_owner", "idx_match_runs_trial_session"),
    ("processing_jobs", "ck_processing_jobs_exactly_one_owner", "idx_processing_jobs_trial_session"),
]


def upgrade() -> None:
    # ── trial_sessions ──────────────────────────────────────────────
    op.create_table(
        "trial_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by_user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
    )
    op.create_index("idx_trial_sessions_expires", "trial_sessions", ["expires_at"])

    # ── Add trial_session_id + relax user_id on each trial-eligible table ──
    for table, constraint_name, index_name in _TRIAL_ELIGIBLE_TABLES:
        op.add_column(
            table,
            sa.Column("trial_session_id", postgresql.UUID(as_uuid=False),
                      sa.ForeignKey("trial_sessions.id"), nullable=True),
        )
        op.create_index(index_name, table, ["trial_session_id"])

    # processing_jobs.user_id is already nullable (see 001_initial_phase1) —
    # only the other four tables need the column relaxed.
    op.alter_column("cv_files", "user_id", nullable=True)
    op.alter_column("cv_profile_versions", "user_id", nullable=True)
    op.alter_column("job_posts", "user_id", nullable=True)
    op.alter_column("match_runs", "user_id", nullable=True)

    for table, constraint_name, _ in _TRIAL_ELIGIBLE_TABLES:
        op.create_check_constraint(constraint_name, table, _OWNER_CHECK_SQL)


def downgrade() -> None:
    for table, constraint_name, index_name in _TRIAL_ELIGIBLE_TABLES:
        op.drop_constraint(constraint_name, table, type_="check")
        op.drop_index(index_name, table_name=table)
        op.drop_column(table, "trial_session_id")

    op.alter_column("match_runs", "user_id", nullable=False)
    op.alter_column("job_posts", "user_id", nullable=False)
    op.alter_column("cv_profile_versions", "user_id", nullable=False)
    op.alter_column("cv_files", "user_id", nullable=False)

    op.drop_index("idx_trial_sessions_expires", table_name="trial_sessions")
    op.drop_table("trial_sessions")
