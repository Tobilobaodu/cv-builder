"""Session hardening + audit_events immutability (Sprint 6 hardening)

Two independent fixes from the security-plan audit:

1. user_sessions.access_token -> access_token_hash. The raw JWT was being
   stored verbatim; get_current_user() now looks a presented token up by
   its SHA-256 hash (hash_token(), same helper already used for
   refresh_token_hash) to check revoked_at, so this column needs to hold
   a hash, not the bearer-usable token itself, and needs an index since
   it's now read on every authenticated request instead of being a
   write-only audit column.

2. audit_events' "append-only" guarantee was previously just a
   SQLAlchemy __table_args__ info dict — documentation, not enforcement.
   A single DB role (cvapp) owns this table in every environment this
   migration runs in, and a role can always GRANT itself back privileges
   REVOKEd from its own objects, so REVOKE UPDATE/DELETE would be a no-op
   speed bump, not a real boundary. A trigger that unconditionally raises
   is enforced regardless of who's connected, and can only be bypassed by
   an explicit DROP TRIGGER (a loud, deliberate DDL statement), not by an
   accidental UPDATE/DELETE in application or migration code.

Revision ID: 012
Revises: 011
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing access_token values are raw JWTs, not hashes — renaming
    # (rather than backfilling a hash) invalidates every session that
    # existed before this migration. Acceptable pre-production: affected
    # users simply need to log in again, and fail-closed is the correct
    # behavior for a security migration anyway.
    op.alter_column("user_sessions", "access_token", new_column_name="access_token_hash")
    op.create_index(
        "idx_sessions_access_token_hash", "user_sessions", ["access_token_hash"], unique=True
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_audit_events_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update
        BEFORE UPDATE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_events_modification();
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_delete
        BEFORE DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_events_modification();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete ON audit_events;")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_events_modification();")
    op.drop_index("idx_sessions_access_token_hash", table_name="user_sessions")
    op.alter_column("user_sessions", "access_token_hash", new_column_name="access_token")
