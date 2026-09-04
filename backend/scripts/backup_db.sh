#!/usr/bin/env bash
# Backup the Postgres database to a timestamped pg_dump custom-format
# file, optionally uploaded to the existing MinIO/S3 bucket
# (app/core/storage.py's same bucket — reused rather than adding a
# second storage client, since it's already wired for both MinIO
# locally and real S3 in production via the same env vars).
#
# Self-managed-Postgres path (see the DR item's RDS-vs-self-managed
# note) — if/when RDS is chosen instead, this script becomes
# unnecessary for routine backups (RDS snapshots automatically); what's
# still worth keeping from this file is the restore-and-verify half
# (restore_db.sh), pointed at a snapshot-restored instance instead of a
# pg_dump file.
#
# Usage: ./backup_db.sh [output_dir]
#   DATABASE_URL   required — the database to back up (owner/superuser
#                  connection; pg_dump only reads, so app_runtime would
#                  also work, but there's no reason to require it).
#   BACKUP_S3_BUCKET, BACKUP_S3_PREFIX   optional — if set, the dump is
#                  also uploaded via `aws s3 cp` (or `mc cp` for MinIO —
#                  see the MINIO_ENDPOINT check below). Uploading is
#                  best-effort: a failed upload does not fail the backup
#                  itself, since a local dump still exists to act on.

set -euo pipefail

OUTPUT_DIR="${1:-./backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="${OUTPUT_DIR}/cv_tailoring_${TIMESTAMP}.dump"
MANIFEST_FILE="${OUTPUT_DIR}/cv_tailoring_${TIMESTAMP}.manifest.json"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set — refusing to guess which database to back up." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "Backing up $(echo "$DATABASE_URL" | sed -E 's#://[^:]+:[^@]+@#://***:***@#') -> $DUMP_FILE"

# -Fc (custom format): compressed, and the only format pg_restore can
# selectively restore from or parallelize — a plain SQL dump can't do
# either, and DR restores are exactly where you want both available.
pg_dump "$DATABASE_URL" -Fc -f "$DUMP_FILE"

# Manifest: what restore_db.sh's post-restore check compares against.
# Row counts per table, not just "the file exists" — a truncated or
# partially-written dump can still produce a file that pg_restore
# accepts without error.
#
# Exact counts (query_to_xml/xpath trick — Postgres has no built-in
# "COUNT(*) for every table" without this or a procedural loop),
# deliberately NOT pg_stat_user_tables.n_live_tup: confirmed live against
# this project's own long-lived dev database that n_live_tup can be
# wildly stale (autovacuum/ANALYZE-dependent) — it reported 1 user where
# an exact count (and the post-restore comparison) both independently
# showed 51. A restore check built on a stats estimate that far off is
# worse than no check: it can fail a genuinely-good restore, or silently
# pass a bad one if both sides happen to be stale in the same direction.
TABLE_COUNTS="$(psql "$DATABASE_URL" -At -c "
  SELECT json_object_agg(t.table_name, t.row_count) FROM (
    SELECT
      c.relname AS table_name,
      (xpath('/row/cnt/text()',
             query_to_xml(format('SELECT count(*) AS cnt FROM %I.%I', n.nspname, c.relname), false, true, '')
      ))[1]::text::bigint AS row_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
    ORDER BY c.relname
  ) t;
")"

cat > "$MANIFEST_FILE" <<EOF
{
  "timestamp": "${TIMESTAMP}",
  "dump_file": "$(basename "$DUMP_FILE")",
  "table_row_counts": ${TABLE_COUNTS:-null}
}
EOF

echo "Manifest written: $MANIFEST_FILE"

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  DEST="s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-db-backups/}$(basename "$DUMP_FILE")"
  echo "Uploading to $DEST (best-effort — a failed upload does not fail this script)"
  if [ -n "${MINIO_ENDPOINT:-}" ]; then
    aws --endpoint-url "$MINIO_ENDPOINT" s3 cp "$DUMP_FILE" "$DEST" || \
      echo "WARNING: upload to $DEST failed — dump is still available locally at $DUMP_FILE" >&2
  else
    aws s3 cp "$DUMP_FILE" "$DEST" || \
      echo "WARNING: upload to $DEST failed — dump is still available locally at $DUMP_FILE" >&2
  fi
  aws $( [ -n "${MINIO_ENDPOINT:-}" ] && echo "--endpoint-url $MINIO_ENDPOINT" ) \
    s3 cp "$MANIFEST_FILE" "s3://${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-db-backups/}$(basename "$MANIFEST_FILE")" || true
fi

echo "Backup complete: $DUMP_FILE"
