"""Enable Postgres Row-Level Security on user-owned tables.

`ENABLE ROW LEVEL SECURITY`, deliberately not `FORCE`. With migration
017's app_runtime role in place, ENABLE is real enforcement for the app
(a non-owner role is never exempt from ENABLE-only RLS) while the owner
role that runs migrations/admin scripts stays unaffected — no need for
FORCE to close an owner-bypass loophole here, since the app never
connects as the owner once DATABASE_URL_RUNTIME_ASYNC is cut over
(app/db/session.py). This is a soft landing on top of a role split that
already does the real work, not a soft landing on its own.

Policies read app.user_id, a transaction-local GUC set by
app/core/security.py's get_scoped_session/get_scoped_session_for_user —
see that file's docstring for why it's re-applied after every commit.
Routes that haven't been switched to one of those two dependencies yet
simply never see the GUC set, so — for as long as the app still connects
as the owner role (pre-cutover) — this migration changes nothing
observable; enforcement only becomes real once both migration 017's role
split AND the get_session -> get_scoped_session* swap in the routers have
shipped and the runtime connection has been cut over.

Table list: every table a router currently applies an ownership check to
(identity_owner_filter or a direct user_id == current_user.id filter) —
confirmed by grep against app/api/v1/*.py, not inferred from the schema.
Two shapes:
  - trial-eligible (nullable user_id + trial_session_id, exactly-one-owner
    CHECK already enforced at the DB layer): cv_files, cv_profile_versions,
    processing_jobs, job_posts, match_runs, tailored_cv_drafts, exports.
  - account-only (user_id NOT NULL, no trial_session_id column):
    cover_letter_workflows, job_post_collections, coverage_reports.

Deliberately NOT covered here (see 018's sibling note in the deferred-
items plan): users, user_sessions, audit_events (not owned resources in
this sense), and child/detail tables reached only via a join from one of
the tables above (cv_experience_items, cover_letter_questions, etc.) —
those need FK-subquery policies, a separate follow-up, not a same-shape
copy-paste of this migration.

Revision ID: 018
Revises: 017
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRIAL_ELIGIBLE_TABLES = (
    "cv_files",
    "cv_profile_versions",
    "processing_jobs",
    "job_posts",
    "match_runs",
    "tailored_cv_drafts",
    "exports",
)

_ACCOUNT_ONLY_TABLES = (
    "cover_letter_workflows",
    "job_post_collections",
    "coverage_reports",
)

_GUC_EXPR = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TRIAL_ELIGIBLE_TABLES:
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"CREATE POLICY {table}_owner_isolation ON {table} "
                f"USING (user_id = {_GUC_EXPR} OR trial_session_id = {_GUC_EXPR})"
            )
        )
    for table in _ACCOUNT_ONLY_TABLES:
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"CREATE POLICY {table}_owner_isolation ON {table} "
                f"USING (user_id = {_GUC_EXPR})"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TRIAL_ELIGIBLE_TABLES + _ACCOUNT_ONLY_TABLES:
        bind.execute(sa.text(f"DROP POLICY IF EXISTS {table}_owner_isolation ON {table}"))
        bind.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
