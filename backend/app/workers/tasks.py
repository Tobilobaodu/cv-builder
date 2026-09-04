"""Celery task enqueue helpers.

These functions are called synchronously from the orchestration service
to dispatch jobs to Celery workers. The actual task implementations
(which run inside Celery worker processes) are in worker_jobs.py.
"""

from datetime import timedelta

from celery import Celery
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Celery app instance — shared between the API (for enqueueing) and workers
celery_app = Celery(
    "cv_tailoring",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Without these, a Redis-side hang (broker unreachable, network partition)
    # blocks the publishing/consuming socket call indefinitely instead of
    # raising — the same class of gap _send_task_with_retry's retry_policy
    # exists to catch, just at the socket layer instead of the publish layer.
    broker_transport_options={
        "socket_timeout": 10,
        "socket_connect_timeout": 10,
    },
    redis_backend_transport_options={
        "socket_timeout": 10,
        "socket_connect_timeout": 10,
    },
    # Autodiscover tasks from worker_jobs (avoids circular import)
    imports=("app.workers.worker_jobs", "app.workers.job_feed_jobs"),
    # Periodic tasks, run by a `celery -A app.workers.tasks beat` process
    # (see docker-compose.yml's `beat` service). Requires a worker consuming
    # the queue named in each entry's task — see `worker_maintenance` for
    # cleanup-expired-trial-sessions's `maintenance` queue.
    beat_schedule={
        "cleanup-expired-trial-sessions": {
            "task": "app.workers.worker_jobs.cleanup_expired_trial_sessions",
            "schedule": timedelta(seconds=settings.trial_session_cleanup_interval_seconds),
        },
        "recover-stalled-jobs": {
            "task": "app.workers.worker_jobs.recover_stalled_jobs",
            "schedule": timedelta(seconds=settings.stalled_job_recovery_interval_seconds),
        },
        "refresh-job-feed": {
            "task": "app.workers.job_feed_jobs.refresh_job_feed",
            "schedule": timedelta(seconds=settings.job_feed_refresh_interval_seconds),
        },
    },
)

_RETRY_POLICY = {
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 3,
}


def _send_task_with_retry(name: str, job_id: str, queue: str) -> str:
    """Publish a Celery task with retry-on-failure and diagnostic logging.

    Without explicit retry, a transient broker connection issue silently
    drops the task — the API commits the DB row but the worker never
    receives the message.  The retry policy here turns that silent loss
    into a raised exception the caller can handle (or a logged retry
    that eventually succeeds).

    Returns the Celery task id of the published message, so callers (and
    the stalled-job recovery task) can record it for diagnosis.
    """
    logger.info("publishing_task", job_id=job_id, task_name=name, queue=queue)
    result = celery_app.send_task(
        name,
        args=[job_id],
        queue=queue,
        retry=True,
        retry_policy=_RETRY_POLICY,
    )
    logger.info("task_publish_confirmed", job_id=job_id, celery_task_id=result.id, queue=queue)
    return result.id


def enqueue_text_extract(job_id: str) -> str:
    """Dispatch a text-extraction job to the text_extract queue.

    Replaces enqueue_docling_extract — steps 3-6 are decommissioned (see
    decommissioned/README.md). This is now the only extraction entrypoint.
    """
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_text_extract", job_id, "text_extract",
    )


# DECOMMISSIONED: enqueue_textract_extract — step removed, see decommissioned/README.md.
# def enqueue_textract_extract(job_id: str) -> str:
#     """Dispatch a Textract extraction job to the textract_extract queue."""
#     return _send_task_with_retry(
#         "app.workers.worker_jobs.process_textract_extract", job_id, "textract_extract",
#     )


# DECOMMISSIONED: enqueue_merge_parse — step removed, see decommissioned/README.md.
# def enqueue_merge_parse(job_id: str) -> str:
#     """Dispatch a merge + parse job to the merge_parse queue."""
#     return _send_task_with_retry(
#         "app.workers.worker_jobs.process_merge_parse", job_id, "merge_parse",
#     )


# ──────────────────────────────────────────────────────────────────────
# Phase 2: Job post ingestion
# ──────────────────────────────────────────────────────────────────────


def enqueue_job_post_fetch(job_id: str) -> str:
    """Dispatch an SSRF-safe URL fetch job to the job_post_fetch queue."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_job_post_fetch", job_id, "job_post_fetch",
    )


# DECOMMISSIONED: enqueue_cv_parse — step removed, see decommissioned/README.md.
# def enqueue_cv_parse(job_id: str) -> str:
#     """Dispatch a CV structured-profile extraction job to the cv_parse queue."""
#     return _send_task_with_retry(
#         "app.workers.worker_jobs.process_cv_parse", job_id, "cv_parse",
#     )


def enqueue_match(job_id: str) -> str:
    """Dispatch a match analysis job to the match queue."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_match", job_id, "match",
    )


def enqueue_job_post_parse(job_id: str) -> str:
    """Dispatch a job post structuring job to the job_post_parse queue."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_job_post_parse", job_id, "job_post_parse",
    )



def enqueue_ats_check(job_id: str) -> str:
    """Dispatch an ATS structural-readiness check to the ats_check queue."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_ats_check", job_id, "ats_check",
    )


def enqueue_cv_analyze(job_id: str) -> str:
    """Dispatch an LLM-based, job-agnostic CV analysis to the cv_analyze
    queue. Auto-chained from process_text_extract — see that task."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_cv_analyze", job_id, "cv_analyze",
    )


# ──────────────────────────────────────────────────────────────────────
# Sprint 3: Tailored CV generation
# ──────────────────────────────────────────────────────────────────────


def enqueue_cv_generate(job_id: str) -> str:
    """Dispatch a tailored CV generation job to the cv_generate queue."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_cv_generate", job_id, "cv_generate",
    )


# ──────────────────────────────────────────────────────────────────────
# Sprint 4: Cover letter generation
# ──────────────────────────────────────────────────────────────────────


def enqueue_cover_letter_generate(job_id: str) -> str:
    """Dispatch a cover letter generation job to the cover_letter_generate queue."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_cover_letter_generate", job_id, "cover_letter_generate",
    )


# ──────────────────────────────────────────────────────────────────────
# Sprint 5: Exports
# ──────────────────────────────────────────────────────────────────────


def enqueue_export(job_id: str) -> str:
    """Dispatch a docx (or application-pack zip) export render job."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_export_docx", job_id, "export",
    )


def enqueue_export_pdf(job_id: str) -> str:
    """Dispatch a docx-to-pdf conversion job — a separate queue from
    enqueue_export since it's a different infra dependency (Gotenberg,
    not just DB/storage) and a different Celery worker consumes it."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_export_pdf", job_id, "export_pdf",
    )


# ──────────────────────────────────────────────────────────────────────
# Sprint 5 / Product Extension #2: Multi-job-post coverage reporting
# ──────────────────────────────────────────────────────────────────────


def enqueue_coverage_report(job_id: str) -> str:
    """Dispatch a coverage-gap aggregation job to the coverage_report queue."""
    return _send_task_with_retry(
        "app.workers.worker_jobs.process_coverage_report", job_id, "coverage_report",
    )
