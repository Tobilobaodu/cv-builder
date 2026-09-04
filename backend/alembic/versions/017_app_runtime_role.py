"""Create app_runtime role for RLS-safe API connections.

Prerequisite for Postgres Row-Level Security (see migration 018). The API
currently connects as `cvapp` (DATABASE_URL_ASYNC), which docker-compose
creates via POSTGRES_USER — in the official Postgres image that role is
the initdb bootstrap superuser. Superusers bypass RLS unconditionally,
regardless of ENABLE or FORCE, so RLS policies would silently do nothing
for the app's own queries until it connects as a role that isn't one.

This migration only creates the role and grants it the same CRUD access
`cvapp` already has (including on tables created by later migrations, via
the default-privileges grant) — zero behavior change on its own. The app
does not start using this role until DATABASE_URL_RUNTIME_ASYNC is set
(app/core/config.py, app/db/session.py) — that's a separate, deliberate
cutover step, not part of this migration.

Revision ID: 017
Revises: 016
"""
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLE_NAME = "app_runtime"


def upgrade() -> None:
    bind = op.get_bind()
    password = os.environ.get("APP_RUNTIME_DB_PASSWORD", "app_runtime_local")

    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": _ROLE_NAME},
    ).scalar()
    if not role_exists:
        bind.execute(
            sa.text(
                f"CREATE ROLE {_ROLE_NAME} LOGIN PASSWORD :password "
                "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            ),
            {"password": password},
        )

    # Grants apply per-database, so these run every time this migration is
    # applied to a given database (dev, test) even though CREATE ROLE
    # above only fires once cluster-wide.
    bind.execute(sa.text(f"GRANT USAGE ON SCHEMA public TO {_ROLE_NAME}"))
    bind.execute(
        sa.text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE "
            f"ON ALL TABLES IN SCHEMA public TO {_ROLE_NAME}"
        )
    )
    bind.execute(
        sa.text(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_ROLE_NAME}"
        )
    )
    # So tables/sequences added by migrations after this one are usable by
    # app_runtime automatically, without a matching GRANT in every future
    # migration. Applies to objects the *current* (migration-running) role
    # creates, which is the owner role — exactly the one that runs
    # migrations here.
    bind.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_ROLE_NAME}"
        )
    )
    bind.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {_ROLE_NAME}"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {_ROLE_NAME}"
        )
    )
    bind.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"REVOKE USAGE, SELECT ON SEQUENCES FROM {_ROLE_NAME}"
        )
    )
    bind.execute(
        sa.text(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {_ROLE_NAME}")
    )
    bind.execute(
        sa.text(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {_ROLE_NAME}")
    )
    bind.execute(sa.text(f"REVOKE USAGE ON SCHEMA public FROM {_ROLE_NAME}"))
    # Cluster-wide — only drop if this is the last database using it.
    # Left as a manual step rather than DROP ROLE here: this migration
    # runs against both the dev and test databases, and the role must
    # survive a downgrade of just one of them.
