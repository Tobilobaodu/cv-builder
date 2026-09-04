"""Live-DB tests for the job-post-collection / coverage-report trigger
endpoints (Sprint 5 / Product Extension #2). Mirrors
test_exports_endpoints.py's/test_tailored_cv_endpoints.py's pattern:
own create_async_engine(..., poolclass=NullPool), no conftest.py, call
route functions directly. Does NOT invoke the Celery worker's actual
aggregation logic (test_coverage_report_worker_live.py covers that) —
this file proves the DB wiring, ownership/IDOR checks, the collection
size cap, and the cvId-resolves-through-CvFile-not-directly detail.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.api.v1.coverage import (
    create_collection, get_coverage_report, list_collections, trigger_coverage_report,
)
from app.schemas.coverage import CoverageReportTriggerRequest, CreateCollectionRequest
from app.db.models import (
    CoverageReport, CvFile, CvProfile, CvProfileVersion, JobPost,
    JobPostCollection, ProcessingJob, User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)

_ip_counter = 0


def _fake_request():
    global _ip_counter
    _ip_counter += 1
    return SimpleNamespace(client=SimpleNamespace(host=f"10.80.0.{_ip_counter}"), headers={})


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


async def _job_post(session, user):
    jp = JobPost(
        id=str(uuid.uuid4()), user_id=user.id, source_type="text",
        raw_text="Python engineer wanted" * 5, status="structured",
    )
    session.add(jp)
    await session.flush()
    return jp


async def _parsed_cv(session, user):
    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=user.id, filename="cv.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="parsed",
    )
    session.add(cv_file)
    await session.flush()
    pv = CvProfileVersion(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, user_id=user.id, version_number=1,
        profile_hash=uuid.uuid4().hex, schema_version="1.0", structured_payload={"basics": {}},
    )
    session.add(pv)
    await session.flush()
    profile = CvProfile(id=str(uuid.uuid4()), cv_file_id=cv_file.id, current_version_id=pv.id)
    session.add(profile)
    await session.flush()
    return cv_file, pv


# ═══════════════════════════════════════════════════════════════════════
# POST /job-post-collections
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_create_collection_succeeds_for_owned_job_posts():
    async with _test_session_factory() as s:
        user = await _user(s, "col1")
        jp1 = await _job_post(s, user)
        jp2 = await _job_post(s, user)
        await s.commit()
        jp1_id, jp2_id = jp1.id, jp2.id

    async with _test_session_factory() as s:
        result = await create_collection(
            body=CreateCollectionRequest(name="Roles I want", job_post_ids=[jp1_id, jp2_id]),
            current_user=user, session=s,
        )
        assert result.name == "Roles I want"
        assert set(result.job_post_ids) == {jp1_id, jp2_id}


@pytest.mark.asyncio(loop_scope="function")
async def test_create_collection_rejects_job_post_owned_by_someone_else():
    async with _test_session_factory() as s:
        owner = await _user(s, "col2owner")
        jp = await _job_post(s, owner)
        await s.commit()
        jp_id = jp.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "col2attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await create_collection(
                body=CreateCollectionRequest(name="Not mine", job_post_ids=[jp_id]),
                current_user=attacker, session=s,
            )
        assert exc.value.status_code == 404


def test_create_collection_request_rejects_more_than_50_job_posts():
    with pytest.raises(Exception):
        CreateCollectionRequest(name="Too many", job_post_ids=[str(uuid.uuid4()) for _ in range(51)])


def test_create_collection_request_rejects_empty_job_post_list():
    with pytest.raises(Exception):
        CreateCollectionRequest(name="Empty", job_post_ids=[])


# ═══════════════════════════════════════════════════════════════════════
# GET /job-post-collections
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_list_collections_scoped_to_current_user():
    async with _test_session_factory() as s:
        user = await _user(s, "list1")
        other = await _user(s, "list1other")
        jp = await _job_post(s, user)
        jp_other = await _job_post(s, other)
        await s.commit()
        jp_id, jp_other_id = jp.id, jp_other.id

    async with _test_session_factory() as s:
        await create_collection(
            body=CreateCollectionRequest(name="Mine", job_post_ids=[jp_id]),
            current_user=user, session=s,
        )
    async with _test_session_factory() as s:
        await create_collection(
            body=CreateCollectionRequest(name="Not mine", job_post_ids=[jp_other_id]),
            current_user=other, session=s,
        )

    async with _test_session_factory() as s:
        result = await list_collections(limit=50, offset=0, current_user=user, session=s)
        assert len(result) == 1
        assert result[0].name == "Mine"


# ═══════════════════════════════════════════════════════════════════════
# POST /job-post-collections/{collectionId}/coverage-report
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_trigger_report_resolves_cv_id_through_cv_file_not_directly():
    """cvId in the request body is a CvFile id, not a CvProfileVersion id
    directly — same resolution cover_letters.py::start_workflow already
    uses. Getting this backwards is an easy, real mistake."""
    async with _test_session_factory() as s:
        user = await _user(s, "trig1")
        jp = await _job_post(s, user)
        cv_file, pv = await _parsed_cv(s, user)
        await s.commit()
        jp_id, cv_file_id, pv_id = jp.id, cv_file.id, pv.id

    async with _test_session_factory() as s:
        collection = await create_collection(
            body=CreateCollectionRequest(name="Roles", job_post_ids=[jp_id]),
            current_user=user, session=s,
        )
        collection_id = collection.id

    async with _test_session_factory() as s:
        result = await trigger_coverage_report(
            collectionId=collection_id, request=_fake_request(),
            body=CoverageReportTriggerRequest(cv_id=cv_file_id),
            current_user=user, session=s,
        )
        assert result.status == "queued"

    async with _test_session_factory() as verify_s:
        report_result = await verify_s.execute(
            select(CoverageReport).where(CoverageReport.collection_id == collection_id)
        )
        report = report_result.scalar_one()
        assert report.cv_profile_version_id == pv_id, "must resolve to the CvProfileVersion, not the raw cvId"
        assert report.status == "pending"

        job_result = await verify_s.execute(
            select(ProcessingJob).where(ProcessingJob.source_entity_id == report.id)
        )
        job = job_result.scalar_one()
        assert job.job_type == "coverage_report"


@pytest.mark.asyncio(loop_scope="function")
async def test_trigger_report_404s_for_unparsed_cv():
    async with _test_session_factory() as s:
        user = await _user(s, "trig2")
        jp = await _job_post(s, user)
        await s.commit()
        jp_id = jp.id

    async with _test_session_factory() as s:
        collection = await create_collection(
            body=CreateCollectionRequest(name="Roles", job_post_ids=[jp_id]),
            current_user=user, session=s,
        )
        collection_id = collection.id

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await trigger_coverage_report(
                collectionId=collection_id, request=_fake_request(),
                body=CoverageReportTriggerRequest(cv_id=str(uuid.uuid4())),
                current_user=user, session=s,
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_trigger_report_rejects_wrong_owner_collection():
    async with _test_session_factory() as s:
        owner = await _user(s, "trig3owner")
        jp = await _job_post(s, owner)
        cv_file, pv = await _parsed_cv(s, owner)
        await s.commit()
        jp_id, cv_file_id = jp.id, cv_file.id

    async with _test_session_factory() as s:
        collection = await create_collection(
            body=CreateCollectionRequest(name="Roles", job_post_ids=[jp_id]),
            current_user=owner, session=s,
        )
        collection_id = collection.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "trig3attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await trigger_coverage_report(
                collectionId=collection_id, request=_fake_request(),
                body=CoverageReportTriggerRequest(cv_id=cv_file_id),
                current_user=attacker, session=s,
            )
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# GET /coverage-reports/{reportId}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_get_report_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "get1owner")
        jp = await _job_post(s, owner)
        cv_file, pv = await _parsed_cv(s, owner)
        await s.commit()
        jp_id, cv_file_id = jp.id, cv_file.id

    async with _test_session_factory() as s:
        collection = await create_collection(
            body=CreateCollectionRequest(name="Roles", job_post_ids=[jp_id]),
            current_user=owner, session=s,
        )
        await trigger_coverage_report(
            collectionId=collection.id, request=_fake_request(),
            body=CoverageReportTriggerRequest(cv_id=cv_file_id),
            current_user=owner, session=s,
        )

    async with _test_session_factory() as verify_s:
        report_result = await verify_s.execute(
            select(CoverageReport).where(CoverageReport.collection_id == collection.id)
        )
        report_id = report_result.scalar_one().id

    async with _test_session_factory() as s:
        attacker = await _user(s, "get1attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await get_coverage_report(reportId=report_id, current_user=attacker, session=s)
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_get_report_returns_pending_status_before_worker_runs():
    async with _test_session_factory() as s:
        user = await _user(s, "get2")
        jp = await _job_post(s, user)
        cv_file, pv = await _parsed_cv(s, user)
        await s.commit()
        jp_id, cv_file_id = jp.id, cv_file.id

    async with _test_session_factory() as s:
        collection = await create_collection(
            body=CreateCollectionRequest(name="Roles", job_post_ids=[jp_id]),
            current_user=user, session=s,
        )
        await trigger_coverage_report(
            collectionId=collection.id, request=_fake_request(),
            body=CoverageReportTriggerRequest(cv_id=cv_file_id),
            current_user=user, session=s,
        )

    async with _test_session_factory() as verify_s:
        report_result = await verify_s.execute(
            select(CoverageReport).where(CoverageReport.collection_id == collection.id)
        )
        report_id = report_result.scalar_one().id

    async with _test_session_factory() as s:
        response = await get_coverage_report(reportId=report_id, current_user=user, session=s)
        assert response.status == "pending"
        assert response.aggregate_gaps == []
