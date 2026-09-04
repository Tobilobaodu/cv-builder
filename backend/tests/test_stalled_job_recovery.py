"""Live-DB tests for the stalled-job recovery task (outbox/recovery).

A processing job can be orphaned between the API producer and the
Celery/Redis broker — send_task() returns without error but the worker
never picks the message up. recover_stalled_jobs() finds such jobs
(status pending/queued, no started_at, older than a min-age window) and
republishes them, bounded by publish_attempts.

These tests monkeypatch the enqueue mapping (never touching a real Celery
broker) and prove the idempotency property directly: running recovery
twice against the same stuck job must not double-publish it.

app.workers.worker_jobs imports docling at module level — stubbed below,
same pattern as test_worker_jobs_cv_parse_new_sections_live.py.
"""

import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

if "docling" not in sys.modules:
    _base_models = types.ModuleType("docling.datamodel.base_models")
    _base_models.InputFormat = object
    _pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")
    _pipeline_options.PdfPipelineOptions = object
    _document_converter = types.ModuleType("docling.document_converter")
    _document_converter.DocumentConverter = object
    _document_converter.PdfFormatOption = object
    _document_converter.WordFormatOption = object
    _docling_core_io = types.ModuleType("docling_core.types.io")
    _docling_core_io.DocumentStream = object

    sys.modules["docling"] = types.ModuleType("docling")
    sys.modules["docling.datamodel"] = types.ModuleType("docling.datamodel")
    sys.modules["docling.datamodel.base_models"] = _base_models
    sys.modules["docling.datamodel.pipeline_options"] = _pipeline_options
    sys.modules["docling.document_converter"] = _document_converter
    sys.modules["docling_core"] = types.ModuleType("docling_core")
    sys.modules["docling_core.types"] = types.ModuleType("docling_core.types")
    sys.modules["docling_core.types.io"] = _docling_core_io

import pytest

from app.workers.worker_jobs import _get_sync_session, recover_stalled_jobs
from app.db.models import ProcessingJob, User


def _seed_stalled_job(*, minutes_old=10, job_type="cv_generate", status="pending"):
    session = _get_sync_session()
    try:
        user = User(
            id=str(uuid.uuid4()),
            email=f"{uuid.uuid4().hex[:8]}@test.example",
            password_hash="fake",
            status="active",
        )
        session.add(user)
        session.flush()
        job = ProcessingJob(
            id=str(uuid.uuid4()),
            job_type=job_type,
            source_entity_type="tailored_cv_draft",
            source_entity_id=str(uuid.uuid4()),
            user_id=user.id,
            status=status,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_old),
        )
        session.add(job)
        session.commit()
        return str(job.id)
    finally:
        session.close()


def test_republishes_stalled_job_once(monkeypatch):
    """The core idempotency property: recovery republishes a stuck job, and
    a second immediate run does NOT republish it again."""
    job_id = _seed_stalled_job()
    calls = []

    def fake_enqueue(jid):
        calls.append(jid)
        return "fake-celery-task-id"

    monkeypatch.setattr(
        "app.workers.worker_jobs._JOB_TYPE_TO_ENQUEUE",
        {"cv_generate": fake_enqueue},
    )

    first = recover_stalled_jobs()
    assert first["republished"] == 1
    assert calls == [job_id]

    second = recover_stalled_jobs()
    assert second["republished"] == 0
    assert calls == [job_id]  # still exactly one publish

    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        assert job.publish_attempts == 1
        assert job.published_at is not None
        assert job.celery_task_id == "fake-celery-task-id"
    finally:
        session.close()


def test_ignores_recent_and_started_jobs(monkeypatch):
    """Only old, never-started jobs are eligible — a just-created job or an
    already-started one must be left alone."""
    recent_id = _seed_stalled_job(minutes_old=0)
    started_id = _seed_stalled_job(minutes_old=10)
    session = _get_sync_session()
    try:
        started_job = session.get(ProcessingJob, started_id)
        started_job.started_at = datetime.now(timezone.utc)
        started_job.status = "processing"
        session.commit()
    finally:
        session.close()

    calls = []

    def fake_enqueue(jid):
        calls.append(jid)
        return "fake-celery-task-id"

    monkeypatch.setattr(
        "app.workers.worker_jobs._JOB_TYPE_TO_ENQUEUE",
        {"cv_generate": fake_enqueue},
    )

    result = recover_stalled_jobs()
    assert result["republished"] == 0
    assert calls == []


def test_unknown_job_type_is_skipped(monkeypatch):
    job_id = _seed_stalled_job(job_type="no_such_type")
    monkeypatch.setattr(
        "app.workers.worker_jobs._JOB_TYPE_TO_ENQUEUE",
        {},
    )
    result = recover_stalled_jobs()
    assert result["republished"] == 0

    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        assert job.publish_attempts == 0
        assert job.published_at is None
    finally:
        session.close()


def test_disabled_flag_skips_recovery(monkeypatch):
    from app.core.config import settings
    job_id = _seed_stalled_job()
    monkeypatch.setattr(settings, "stalled_job_recovery_enabled", False)

    result = recover_stalled_jobs()
    assert result == {"republished": 0}

    session = _get_sync_session()
    try:
        job = session.get(ProcessingJob, job_id)
        assert job.published_at is None
        assert job.publish_attempts == 0
    finally:
        session.close()
