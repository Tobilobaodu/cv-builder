"""Refresh all 5 job-feed sources and upsert into feed_job_postings.

`on_conflict_do_nothing` keyed on (source, external_id) — migration 020's
unique constraint — makes re-fetching the same listing a safe no-op
instead of a duplicate row; DO NOTHING rather than DO UPDATE because a
listing's own content (title, description, ...) doesn't need refreshing
once ingested, only its *existence* needs re-confirming.

Plain sync SQLAlchemy Session, not the async engine — called from a
Celery worker task (app/workers/job_feed_jobs.py), same as every other
worker_jobs.py task.
"""

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import FeedJobPosting
from app.services.job_feed.adapters import ALL_ADAPTERS

logger = get_logger(__name__)


def refresh_all_sources(session: Session) -> dict[str, int]:
    """Fetch every configured source and upsert new listings. Returns
    {source: rows_actually_inserted} — a source that errors or is
    unconfigured contributes 0, not an exception (see adapters.py's
    per-adapter error handling)."""
    inserted_counts: dict[str, int] = {}

    for source, fetch in ALL_ADAPTERS.items():
        postings = fetch()
        if not postings:
            inserted_counts[source] = 0
            continue

        rows = [dict(p) for p in postings]
        stmt = pg_insert(FeedJobPosting).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["source", "external_id"]
        )
        result = session.execute(stmt)
        inserted_counts[source] = result.rowcount or 0
        logger.info(
            "job_feed_source_refreshed", source=source,
            fetched=len(postings), inserted=inserted_counts[source],
        )

    return inserted_counts
