"""classify_error must not reclassify a worker's own explicit judgement.

Found live: process_text_extract's new malware-scan step (jbs-solution-
sheet.md S6) raises RetryableWorkerError directly when ClamAV is
temporarily unreachable — and classify_error, called by the outer except
block to decide is_final_attempt, silently turned that into
PermanentWorkerError, since neither of its heuristics (TimeoutError/
ConnectionError, retryable HTTP status) recognises the custom types. A
transient scanner outage was permanently failing the CV instead of
retrying.
"""

from app.core.job_states import (
    PermanentWorkerError,
    RetryableWorkerError,
    classify_error,
)


def test_retryable_worker_error_is_classified_as_itself():
    assert classify_error(RetryableWorkerError("scanner unavailable")) is RetryableWorkerError


def test_permanent_worker_error_is_classified_as_itself():
    assert classify_error(PermanentWorkerError("malware detected")) is PermanentWorkerError


def test_timeout_error_still_classified_as_retryable():
    assert classify_error(TimeoutError("timed out")) is RetryableWorkerError


def test_unrecognised_error_defaults_to_permanent():
    assert classify_error(ValueError("something else")) is PermanentWorkerError


def test_retryable_http_status_still_classified_as_retryable():
    class _HttpError(Exception):
        status_code = 503

    assert classify_error(_HttpError()) is RetryableWorkerError
