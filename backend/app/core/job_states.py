"""Explicit ProcessingJob state machine and worker error classification.

ProcessingJob.status has always been a free-text column mutated inline
(`job.status = "completed"`) at 40+ call sites across app/workers/worker_jobs.py
and app/services/orchestration.py, with no central definition of which
transitions are legal. This module is that central definition — it doesn't
change what any call site does today, it gives new/changed call sites a
validated helper (transition_job_status) instead of another bare string
assignment. See its use in orchestration.py and process_docling_extract for
the intended rollout pattern; the remaining inline assignments are an
intentionally separate, larger follow-up rather than a blind mechanical
replace across every worker task in one pass.

Status values match what the codebase already writes (String(20) column,
app/db/models.py:ProcessingJob) — "processing"/"completed", not the
"running"/"succeeded" naming a from-scratch design might pick, since
renaming would touch every route response and every test's status string.
"""

from enum import StrEnum


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


# Both PENDING and QUEUED are used as a job's initial status depending on
# call site (orchestration.create_processing_job uses QUEUED;
# tailored_cvs.py/cover_letters.py/exports.py/job_posts.py/matches.py/
# coverage.py create ProcessingJob rows directly with PENDING) — both are
# legitimate starting points, not a bug to unify here.
ALLOWED_TRANSITIONS: dict[ProcessingStatus, set[ProcessingStatus]] = {
    ProcessingStatus.PENDING: {
        ProcessingStatus.QUEUED, ProcessingStatus.PROCESSING, ProcessingStatus.FAILED,
    },
    ProcessingStatus.QUEUED: {
        ProcessingStatus.PROCESSING, ProcessingStatus.RETRYING, ProcessingStatus.FAILED,
    },
    ProcessingStatus.PROCESSING: {
        ProcessingStatus.COMPLETED, ProcessingStatus.RETRYING, ProcessingStatus.FAILED,
    },
    ProcessingStatus.RETRYING: {ProcessingStatus.PROCESSING, ProcessingStatus.FAILED},
    ProcessingStatus.COMPLETED: set(),
    ProcessingStatus.FAILED: set(),
}


def assert_transition(current: str, target: str) -> None:
    """Raise ValueError if `current` -> `target` isn't a legal ProcessingJob
    status transition. Both args accept plain strings (the column type)
    so callers don't need to import ProcessingStatus just to check."""
    try:
        current_status = ProcessingStatus(current)
        target_status = ProcessingStatus(target)
    except ValueError as e:
        raise ValueError(f"Unknown ProcessingJob status in transition check: {e}") from e
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(f"Invalid ProcessingJob transition: {current_status} -> {target_status}")


def transition_job_status(job, target: str, *, error: str | None = None) -> None:
    """Validate and apply a ProcessingJob status transition in place.

    Does not commit — callers already control their own commit boundary
    (matches every existing call site's pattern of batching the status
    change with other row updates before a single session.commit()).
    """
    assert_transition(job.status, target)
    job.status = target
    if error is not None:
        job.last_error = error


class RetryableWorkerError(Exception):
    """A worker task failure likely to succeed on retry (timeout, connection
    reset, provider 429/5xx) — distinct from PermanentWorkerError so retry
    policy can stop wasting attempts on errors retrying can't fix."""


class PermanentWorkerError(Exception):
    """A worker task failure that retrying will not fix (validation error,
    malformed input, 4xx other than 429)."""


_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


def classify_error(error: BaseException) -> type[Exception]:
    """Best-effort classification of a caught exception as retryable or
    permanent, for worker tasks that want to distinguish them (e.g. to
    avoid burning retry attempts on a permanent failure). Purely advisory —
    it returns a type, it doesn't raise or re-wrap; the caller decides what
    to do with the classification.

    A caller that already raised RetryableWorkerError/PermanentWorkerError
    directly (rather than some other exception this function has to guess
    about) gets that exact classification back unchanged — checked first,
    before the heuristics below, so a worker's own explicit judgement about
    its own failure is never second-guessed into the wrong bucket. Found
    live: a bare `raise RetryableWorkerError(...)` was silently reclassified
    as permanent here, since neither heuristic below recognises either
    custom type — this function's actual behaviour for its own inputs,
    not merely underspecified."""
    if isinstance(error, (RetryableWorkerError, PermanentWorkerError)):
        return type(error)
    if isinstance(error, (TimeoutError, ConnectionError)):
        return RetryableWorkerError
    status_code = getattr(error, "status_code", None) or getattr(error, "http_status", None)
    if status_code in _RETRYABLE_HTTP_STATUS:
        return RetryableWorkerError
    return PermanentWorkerError
