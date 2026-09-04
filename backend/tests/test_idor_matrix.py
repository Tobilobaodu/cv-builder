"""IDOR matrix — parametrized cross-user denial over every ID-scoped route.

Security-plan §3 calls this "the single highest-value automated security test
for this system." Previously ~8 of ~39 ID-scoped endpoints had an explicit
cross-user-denial test, scattered across five files with a repeated
attacker = await _user(...) pattern. This file centralises it: one fixture
builds a full resource chain for user A, then a single parametrized test runs
user B's identity against every ID-scoped route and asserts a consistent 404
(never a mix of 403/404/500 — an inconsistency is itself an information-
disclosure bug per §3).

Calls route functions directly (async, no TestClient), matching the
established live-DB pattern (own NullPool engine, no conftest.py).
"""

import sys
import time
import types
import uuid

import pytest
from fastapi import HTTPException
from prometheus_client import REGISTRY
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

# ── Stub magic before importing cvs.py (which imports file_validation) ─────
# Same pattern as test_auth_endpoints.py: python-magic's native lib segfaults
# the interpreter on this host; only the Docker image has working libmagic.
if "magic" not in sys.modules:
    _magic = types.ModuleType("magic")
    _magic.MagicException = Exception
    _magic.from_buffer = lambda *a, **k: "application/octet-stream"
    _magic.from_file = lambda *a, **k: "application/octet-stream"
    sys.modules["magic"] = _magic

from app.core.config import settings
from app.core.security import RequestIdentity
from app.api.v1.cvs import (
    get_cv, delete_cv, reprocess_cv, get_cv_raw_text, get_cv_extraction_detail,
    get_cv_parsed_profile, run_ats_check_for_cv, get_ats_check,
)
from app.api.v1.job_posts import get_job_post, reprocess_job_post
from app.api.v1.jobs import get_job_status
from app.api.v1.matches import get_match
from app.api.v1.tailored_cvs import (
    create_tailored_cv, get_tailored_cv, regenerate_tailored_cv, approve_tailored_cv,
)
from app.api.v1.cover_letters import (
    get_questions, submit_answers, get_draft, regenerate, approve,
)
from app.api.v1.exports import (
    export_cv, export_cover_letter, get_export, download_export, export_pdf,
)
from app.api.v1.coverage import trigger_coverage_report, get_coverage_report
from app.api.v1.audit import get_audit_events
from app.schemas.tailored_cv import RegenerateRequest
from app.schemas.cover_letter import StartWorkflowRequest, SubmitAnswersRequest
from app.schemas.export import CreateExportRequest
from app.schemas.coverage import CoverageReportTriggerRequest
from app.db.models import (
    AuditEvent, CoverLetterDraft, CoverLetterWorkflow, CoverageReport, CvFile,
    CvProfile, CvProfileVersion, Export, JobPost, JobPostCollection,
    JobPostProfile, MatchRun, ProcessingJob, TailoredCvDraft, User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)

_ip_counter = 0


def _fake_request():
    global _ip_counter
    _ip_counter += 1
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host=f"10.99.0.{_ip_counter}"), headers={}
    )


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Route functions check rate limits before ownership; reset the module-global
    limiter state between tests so one route's attempts can't 429 another."""
    import app.core.rate_limit as rl

    rl._attempts.clear()
    rl._blocked.clear()
    rl._last_cleanup = time.time()
    yield
    rl._attempts.clear()
    rl._blocked.clear()
    rl._last_cleanup = time.time()


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


async def _build_full_chain(session, owner):
    """Seed a complete owner resource chain and return its IDs as a dict."""
    ids = {}

    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=owner.id, filename="cv.pdf",
        mime_type="application/pdf", file_size=1, storage_key=str(uuid.uuid4()),
        status="parsed",
    )
    session.add(cv_file)
    await session.flush()
    ids["cv_id"] = cv_file.id

    pv = CvProfileVersion(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, user_id=owner.id,
        version_number=1, profile_hash=uuid.uuid4().hex, schema_version="1.0",
        structured_payload={"basics": {"name": "Jane Doe"}},
    )
    session.add(pv)
    await session.flush()
    ids["profile_version_id"] = pv.id

    job_post = JobPost(
        id=str(uuid.uuid4()), user_id=owner.id, source_type="text",
        raw_text="Python engineer wanted" * 10, status="structured",
    )
    session.add(job_post)
    await session.flush()
    ids["job_post_id"] = job_post.id

    jp_profile = JobPostProfile(
        id=str(uuid.uuid4()), job_post_id=job_post.id, job_title="Engineer",
        employer="Acme", required_skills=["Python"],
    )
    session.add(jp_profile)
    await session.flush()

    match = MatchRun(
        id=str(uuid.uuid4()), user_id=owner.id, cv_profile_version_id=pv.id,
        job_post_profile_id=jp_profile.id, status="completed", score=0.5,
    )
    session.add(match)
    await session.flush()
    ids["match_id"] = match.id

    proc_job = ProcessingJob(
        id=str(uuid.uuid4()), user_id=owner.id, job_type="cv_parse",
        source_entity_type="cv_file", source_entity_id=cv_file.id, status="completed",
    )
    session.add(proc_job)
    await session.flush()
    ids["job_id"] = proc_job.id

    draft = TailoredCvDraft(
        id=str(uuid.uuid4()), user_id=owner.id, match_run_id=match.id,
        version_number=1, status="approved", content_json={},
    )
    session.add(draft)
    await session.flush()
    ids["draft_id"] = draft.id

    wf = CoverLetterWorkflow(
        id=str(uuid.uuid4()), user_id=owner.id, cv_profile_version_id=pv.id,
        job_post_profile_id=jp_profile.id, status="approved", current_step=3,
        total_steps=3, question_set_version=1,
    )
    session.add(wf)
    await session.flush()
    ids["workflow_id"] = wf.id

    cl_draft = CoverLetterDraft(
        id=str(uuid.uuid4()), workflow_id=wf.id, version_number=1,
        status="approved", body_text="Dear Hiring Manager, ...",
    )
    session.add(cl_draft)
    await session.flush()

    export = Export(
        id=str(uuid.uuid4()), user_id=owner.id, export_type="cv", source_id=draft.id,
        format="docx", status="completed", storage_key="some/key",
        template_id="standard",
    )
    session.add(export)
    await session.flush()
    ids["export_id"] = export.id

    collection = JobPostCollection(
        id=str(uuid.uuid4()), user_id=owner.id, name="col",
        job_post_ids=[job_post.id],
    )
    session.add(collection)
    await session.flush()
    ids["collection_id"] = collection.id

    report = CoverageReport(
        id=str(uuid.uuid4()), user_id=owner.id, cv_profile_version_id=pv.id,
        collection_id=collection.id, match_run_ids=[match.id], aggregate_gaps=[],
        status="completed",
    )
    session.add(report)
    await session.flush()
    ids["report_id"] = report.id

    return ids


async def _build_scenario():
    """Build user A's full chain, then register user B. Returns (attacker, ids)."""
    async with _test_session_factory() as s:
        owner = await _user(s, "owner")
        ids = await _build_full_chain(s, owner)
        await s.commit()
    async with _test_session_factory() as s:
        attacker = await _user(s, "attacker")
        await s.commit()
    return attacker, ids


def _identity(attacker):
    return RequestIdentity(user=attacker, trial_session=None)


# ── One call function per ID-scoped route. Each runs user B's identity ─────
# against user A's resource id — expected to 404.


async def _call_get_cv(a, ids, s):
    await get_cv(cv_id=ids["cv_id"], identity=_identity(a), session=s)


async def _call_delete_cv(a, ids, s):
    await delete_cv(cv_id=ids["cv_id"], current_user=a, session=s)


async def _call_reprocess_cv(a, ids, s):
    await reprocess_cv(request=_fake_request(), cv_id=ids["cv_id"], current_user=a, session=s)


async def _call_get_cv_raw_text(a, ids, s):
    await get_cv_raw_text(cv_id=ids["cv_id"], identity=_identity(a), session=s)


async def _call_get_cv_extraction_detail(a, ids, s):
    await get_cv_extraction_detail(cv_id=ids["cv_id"], current_user=a, session=s)


async def _call_get_cv_parsed_profile(a, ids, s):
    await get_cv_parsed_profile(cv_id=ids["cv_id"], identity=_identity(a), session=s)


async def _call_run_ats_check(a, ids, s):
    await run_ats_check_for_cv(request=_fake_request(), cv_id=ids["cv_id"], current_user=a, session=s)


async def _call_get_ats_check(a, ids, s):
    await get_ats_check(cv_id=ids["cv_id"], current_user=a, session=s)


async def _call_get_job_post(a, ids, s):
    await get_job_post(jobPostId=ids["job_post_id"], identity=_identity(a), session=s)


async def _call_reprocess_job_post(a, ids, s):
    await reprocess_job_post(request=_fake_request(), jobPostId=ids["job_post_id"], current_user=a, session=s)


async def _call_get_match(a, ids, s):
    await get_match(matchId=ids["match_id"], identity=_identity(a), session=s)


async def _call_get_job_status(a, ids, s):
    await get_job_status(job_id=ids["job_id"], identity=_identity(a), session=s)


async def _call_create_tailored_cv(a, ids, s):
    await create_tailored_cv(matchId=ids["match_id"], request=_fake_request(), identity=_identity(a), session=s)


async def _call_get_tailored_cv(a, ids, s):
    await get_tailored_cv(draftId=ids["draft_id"], identity=_identity(a), session=s)


async def _call_regenerate_tailored_cv(a, ids, s):
    await regenerate_tailored_cv(
        draftId=ids["draft_id"], request=_fake_request(),
        body=RegenerateRequest(), identity=_identity(a), session=s,
    )


async def _call_approve_tailored_cv(a, ids, s):
    await approve_tailored_cv(draftId=ids["draft_id"], identity=_identity(a), session=s)


async def _call_get_questions(a, ids, s):
    await get_questions(workflowId=ids["workflow_id"], current_user=a, session=s)


async def _call_submit_answers(a, ids, s):
    await submit_answers(
        workflowId=ids["workflow_id"], body=SubmitAnswersRequest(answers=[]),
        current_user=a, session=s,
    )


async def _call_get_cl_draft(a, ids, s):
    await get_draft(workflowId=ids["workflow_id"], current_user=a, session=s)


async def _call_cl_regenerate(a, ids, s):
    await regenerate(request=_fake_request(), workflowId=ids["workflow_id"], current_user=a, session=s)


async def _call_cl_approve(a, ids, s):
    await approve(workflowId=ids["workflow_id"], request=_fake_request(), current_user=a, session=s)


async def _call_export_cv(a, ids, s):
    await export_cv(
        draftId=ids["draft_id"], request=_fake_request(), body=CreateExportRequest(),
        identity=_identity(a), session=s,
    )


async def _call_export_cover_letter(a, ids, s):
    await export_cover_letter(
        workflowId=ids["workflow_id"], request=_fake_request(),
        body=CreateExportRequest(), current_user=a, session=s,
    )


async def _call_get_export(a, ids, s):
    await get_export(exportId=ids["export_id"], identity=_identity(a), session=s)


async def _call_download_export(a, ids, s):
    await download_export(exportId=ids["export_id"], identity=_identity(a), session=s)


async def _call_export_pdf(a, ids, s):
    await export_pdf(exportId=ids["export_id"], request=_fake_request(), identity=_identity(a), session=s)


async def _call_trigger_coverage_report(a, ids, s):
    await trigger_coverage_report(
        collectionId=ids["collection_id"], request=_fake_request(),
        body=CoverageReportTriggerRequest(cv_id=ids["cv_id"]), current_user=a, session=s,
    )


async def _call_get_coverage_report(a, ids, s):
    await get_coverage_report(reportId=ids["report_id"], current_user=a, session=s)


ROUTES = [
    ("GET /cvs/{cvId}", _call_get_cv),
    ("DELETE /cvs/{cvId}", _call_delete_cv),
    ("POST /cvs/{cvId}/reprocess", _call_reprocess_cv),
    ("GET /cvs/{cvId}/raw-text", _call_get_cv_raw_text),
    ("GET /cvs/{cvId}/extraction-detail", _call_get_cv_extraction_detail),
    ("GET /cvs/{cvId}/parsed-profile", _call_get_cv_parsed_profile),
    ("POST /cvs/{cvId}/ats-check", _call_run_ats_check),
    ("GET /cvs/{cvId}/ats-check", _call_get_ats_check),
    ("GET /job-posts/{jobPostId}", _call_get_job_post),
    ("POST /job-posts/{jobPostId}/reprocess", _call_reprocess_job_post),
    ("GET /matches/{matchId}", _call_get_match),
    ("GET /jobs/{jobId}", _call_get_job_status),
    ("POST /matches/{matchId}/tailored-cv", _call_create_tailored_cv),
    ("GET /tailored-cvs/{draftId}", _call_get_tailored_cv),
    ("POST /tailored-cvs/{draftId}/regenerate", _call_regenerate_tailored_cv),
    ("POST /tailored-cvs/{draftId}/approve", _call_approve_tailored_cv),
    ("GET /cover-letters/{workflowId}/questions", _call_get_questions),
    ("POST /cover-letters/{workflowId}/answers", _call_submit_answers),
    ("GET /cover-letters/{workflowId}/draft", _call_get_cl_draft),
    ("POST /cover-letters/{workflowId}/regenerate", _call_cl_regenerate),
    ("POST /cover-letters/{workflowId}/approve", _call_cl_approve),
    ("POST /exports/cv/{draftId}", _call_export_cv),
    ("POST /exports/cover-letter/{workflowId}", _call_export_cover_letter),
    ("GET /exports/{exportId}", _call_get_export),
    ("GET /exports/{exportId}/download", _call_download_export),
    ("POST /exports/{exportId}/pdf", _call_export_pdf),
    ("POST /job-post-collections/{collectionId}/coverage-report", _call_trigger_coverage_report),
    ("GET /coverage-reports/{reportId}", _call_get_coverage_report),
]


@pytest.mark.parametrize("route_name,call", ROUTES)
@pytest.mark.asyncio(loop_scope="function")
async def test_cross_user_denied_404(route_name, call):
    attacker, ids = await _build_scenario()
    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await call(attacker, ids, s)
        assert exc.value.status_code == 404, (
            f"{route_name}: expected 404 (consistent denial), "
            f"got {exc.value.status_code}"
        )


def _authz_denied_sample():
    return REGISTRY.get_sample_value("authz_denied_total") or 0.0


@pytest.mark.parametrize("route_name,call", ROUTES)
@pytest.mark.asyncio(loop_scope="function")
async def test_cross_user_denied_increments_authz_counter(route_name, call):
    """§10 alerting depends on authz_denied_total firing for every real IDOR
    denial point, not just the handful of routes that happened to be wired
    when the counter was first added (Sprint 6 audit finding: only 3 of ~28
    routes fed it). Reuses the same ROUTES matrix as test_cross_user_denied_404
    so a route can't pass the 404 check while silently under-reporting here.
    """
    attacker, ids = await _build_scenario()
    before = _authz_denied_sample()
    async with _test_session_factory() as s:
        with pytest.raises(HTTPException):
            await call(attacker, ids, s)
    assert _authz_denied_sample() - before >= 1, (
        f"{route_name}: denied with 404 but did not increment authz_denied_total"
    )


@pytest.mark.parametrize("route_name,call", ROUTES)
@pytest.mark.asyncio(loop_scope="function")
async def test_cross_user_denied_writes_audit_event(route_name, call):
    """Tabletop finding (2026-08-13, see 14-incident-response-runbook.md §5):
    audit_events only ever recorded mutations, never denied-read attempts —
    the runbook's own documented "pull audit_events for the suspect" step
    came back empty for a real IDOR probe, leaving authz_denied_total's bare
    count as the only signal (no per-entity detail). ownership_denied() now
    writes an access_denied AuditEvent; this proves it fires from all 28
    routes, not just the one it was first wired against.
    """
    attacker, ids = await _build_scenario()
    async with _test_session_factory() as s:
        before = (await s.execute(
            select(AuditEvent).where(
                AuditEvent.user_id == attacker.id,
                AuditEvent.event_type == "access_denied",
            )
        )).scalars().all()
        with pytest.raises(HTTPException):
            await call(attacker, ids, s)

    async with _test_session_factory() as s:
        after = (await s.execute(
            select(AuditEvent).where(
                AuditEvent.user_id == attacker.id,
                AuditEvent.event_type == "access_denied",
            )
        )).scalars().all()
    assert len(after) - len(before) >= 1, (
        f"{route_name}: denied with 404 but wrote no access_denied AuditEvent"
    )


# GET /audit/{entityType}/{entityId} is a list endpoint, not a single-resource
# route — its denial semantics are "empty list, no data leaked" rather than
# 404. Folded into this matrix separately so the ownership guarantee is still
# asserted here alongside every other ID-scoped route.


@pytest.mark.asyncio(loop_scope="function")
async def test_audit_cross_user_returns_empty():
    async with _test_session_factory() as s:
        owner = await _user(s, "auditowner")
        e = AuditEvent(
            id=str(uuid.uuid4()), user_id=owner.id, event_type="upload",
            entity_type="cv_file", entity_id=str(uuid.uuid4()), actor_type="user",
        )
        s.add(e)
        await s.commit()
        entity_id = e.entity_id
    async with _test_session_factory() as s:
        attacker = await _user(s, "auditattacker")
        await s.commit()

    async with _test_session_factory() as s:
        events = await get_audit_events(
            entityType="cv_file", entityId=entity_id, limit=200, offset=0,
            current_user=attacker, session=s,
        )

    assert events == []





