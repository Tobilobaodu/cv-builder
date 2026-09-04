#!/usr/bin/env bash
# Restore a pg_dump custom-format file into a *freshly named* database —
# never the live one directly. Same safety posture as
# tests/conftest.py's _assert_test_database guard: refuse to touch
# anything that doesn't look like a disposable target, since a mistaken
# restore onto a live database is exactly the kind of destructive
# action a DR drill exists to rehearse against, not risk causing.
#
# After restoring, runs `alembic upgrade head` (confirms the dump is
# migration-consistent — a dump from before the latest migration should
# still cleanly upgrade) and compares post-restore row counts against
# the backup's manifest, table by table. This is the check that makes
# the drill mean something: pg_restore exiting 0 only proves the file
# was well-formed, not that the data is complete.
#
# Usage: ./restore_db.sh <dump_file> <target_db_name>
#   Requires: DATABASE_URL_ADMIN — a connection to the Postgres server
#   with permission to CREATE/DROP DATABASE (e.g. postgres://cvapp:...@host:5432/postgres),
#   NOT scoped to any single database, since this script creates one.

set -euo pipefail

DUMP_FILE="${1:?Usage: restore_db.sh <dump_file> <target_db_name>}"
TARGET_DB="${2:?Usage: restore_db.sh <dump_file> <target_db_name>}"

if [ -z "${DATABASE_URL_ADMIN:-}" ]; then
  echo "DATABASE_URL_ADMIN is not set — need a connection with CREATE/DROP DATABASE rights, pointed at the 'postgres' maintenance database, not a specific app database." >&2
  exit 1
fi

# The same class of guard conftest.py's _assert_test_database uses: this
# script TRUNCATEs/DROPs the target database on every run (see below),
# so refuse anything that isn't obviously disposable. A real restore
# drill's target should always be named like this — if you need to
# restore over a genuinely live database, that's a different, far more
# careful, manual operation, not this script.
case "$TARGET_DB" in
  *_restore_drill|*_restore_test|*_dr_check)
    ;;
  *)
    echo "Refusing to restore into database '$TARGET_DB' — expected a name ending in '_restore_drill', '_restore_test', or '_dr_check'. This script drops and recreates its target on every run; do not point it at a real database." >&2
    exit 1
    ;;
esac

if [ ! -f "$DUMP_FILE" ]; then
  echo "Dump file not found: $DUMP_FILE" >&2
  exit 1
fi

echo "Dropping and recreating $TARGET_DB (disposable restore target)"
psql "$DATABASE_URL_ADMIN" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS ${TARGET_DB};"
psql "$DATABASE_URL_ADMIN" -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${TARGET_DB} OWNER cvapp;"

# Build the target connection string from the admin URL's host/port/user,
# swapping only the database name — avoids requiring a second env var
# just to say what everyone already knows: same server, different db.
# Plain parameter expansion, not sed: confirmed live that BusyBox sed
# (Alpine, e.g. the postgres:16-alpine image) rejects the GNU-sed-style
# extended regex this used originally ("sed: unmatched '#'") — this
# strips everything after the last '/' instead, which needs no regex
# engine at all and behaves identically in bash, dash, and BusyBox ash.
TARGET_URL="${DATABASE_URL_ADMIN%/*}/${TARGET_DB}"

echo "Restoring $DUMP_FILE -> $TARGET_DB"
pg_restore -d "$TARGET_URL" --no-owner --no-acl -j 4 "$DUMP_FILE"

echo "Running migrations against the restored database (confirms it's migration-consistent)"
DATABASE_URL="$TARGET_URL" python -m alembic upgrade head

echo "Comparing post-restore row counts against the backup manifest"
MANIFEST_FILE="${DUMP_FILE%.dump}.manifest.json"
if [ ! -f "$MANIFEST_FILE" ]; then
  echo "WARNING: no manifest found at $MANIFEST_FILE — skipping row-count verification. A restore without this check is unverified by definition." >&2
  exit 2
fi

python3 - "$TARGET_URL" "$MANIFEST_FILE" <<'PYEOF'
import json
import subprocess
import sys

target_url, manifest_path = sys.argv[1], sys.argv[2]

with open(manifest_path) as f:
    manifest = json.load(f)
expected = manifest.get("table_row_counts") or {}

# Exact counts, matching backup_db.sh's manifest query exactly —
# NOT pg_stat_user_tables.n_live_tup. Confirmed live that n_live_tup can
# be wildly stale on a long-lived database (reported 1 user where an
# exact count showed 51, on this project's own dev DB) — comparing a
# fresh exact count against a stale-estimate manifest produced false
# "RESTORE VERIFICATION FAILED" results for a restore that was actually
# byte-for-byte correct. Both sides of this comparison must use the same
# counting method or a mismatch proves nothing about the restore itself.
result = subprocess.run(
    ["psql", target_url, "-At", "-c", """
      SELECT json_object_agg(t.table_name, t.row_count) FROM (
        SELECT
          c.relname AS table_name,
          (xpath('/row/cnt/text()',
                 query_to_xml(format('SELECT count(*) AS cnt FROM %I.%I', n.nspname, c.relname), false, true, '')
          ))[1]::text::bigint AS row_count
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
      ) t;
    """],
    capture_output=True, text=True, check=True,
)
actual = json.loads(result.stdout.strip() or "{}")

mismatches = []
for table, expected_count in expected.items():
    actual_count = actual.get(table)
    if actual_count != expected_count:
        mismatches.append((table, expected_count, actual_count))

if mismatches:
    print("RESTORE VERIFICATION FAILED — row count mismatches:")
    for table, expected_count, actual_count in mismatches:
        print(f"  {table}: expected ~{expected_count}, got {actual_count}")
    sys.exit(1)

print(f"Restore verified: {len(expected)} tables match the pre-backup manifest within tolerance.")
PYEOF

echo "Restore drill complete and verified: $TARGET_DB"
