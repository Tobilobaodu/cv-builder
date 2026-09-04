"""Celery task: periodic free-API job-feed refresh (item 7).

Runs on the `job_feed` queue (worker_job_feed in docker-compose.yml, the
only job-feed-related worker with public internet egress — mirrors
worker_job_fetch's network posture, not worker_maintenance's). Scheduled
via app.workers.tasks's beat_schedule, same pattern as
cleanup_expired_trial_sessions/recover_stalled_jobs: no route enqueues
this directly, celery beat fires it on an interval.

Own sync engine, not an import from worker_jobs.py's _sync_engine —
mirrors that module's own pattern (Celery tasks are not async) rather
than adding an import edge between two independently-scheduled task
modules.
"""

from celery import shared_task
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.services.job_feed.ingest import refresh_all_sources

logger = get_logger(__name__)

_sync_engine = create_engine(
    settings.database_url,
    pool_timeout=30,
    connect_args={"connect_timeout": 10},
)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="app.workers.job_feed_jobs.refresh_job_feed",
    queue="job_feed",
)
def refresh_job_feed(self) -> None:
    with Session(_sync_engine) as session:
        counts = refresh_all_sources(session)
        session.commit()
    logger.info("job_feed_refresh_complete", **counts)
