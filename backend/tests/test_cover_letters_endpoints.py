"""Live-DB tests for the cover letter workflow endpoints (Sprint 4 async
restructuring).

Mirrors test_tailored_cv_endpoints.py's pattern exactly: own
create_async_engine(..., poolclass=NullPool), no conftest.py, call the
route functions directly (async, no TestClient). Does NOT invoke the
Celery worker or the real OpenAI API — process_cover_letter_generate's
own logic is covered by test_cover_letter_generation.py's fake-LLM-client
tests; this file proves the DB wiring, ownership, and status-transition
logic around it, including the three correctness fixes forced by moving
from synchronous to async generation.

Cover letters stay get_current_user-only (no RequestIdentity/trial
support) — deliberate, per the product-vision account-paywall boundary.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.api.v1.cover_letters import (
    submit_answers, get_draft, regenerate, approve, list_cover_letter_workflows,
    delete_cover_letter_workflow,
)
from app.schemas.cover_letter import SubmitAnswersRequest, AnswerItem
from app.db.models import (
    CoverLetterAnswer, CoverLetterDraft, CoverLetterQuestion, CoverLetterWorkflow,
    CvFile, CvProfileVersion, JobPost, JobPostProfile, ProcessingJob, User,
)

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)

_ip_counter = 0


def _fake_request():
    global _ip_counter
    _ip_counter += 1
    return SimpleNamespace(
        client=SimpleNamespace(host=f"10.78.0.{_ip_counter}"),
        headers={},
    )


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash="fake", status="active",
    )
    session.add(u)
    await session.flush()
    return u


async def _cv_profile_version(session, user):
    cv_file = CvFile(
        id=str(uuid.uuid4()), user_id=user.id, filename="cv.pdf", mime_type="application/pdf",
        file_size=1, storage_key=str(uuid.uuid4()), status="parsed",
    )
    session.add(cv_file)
    await session.flush()
    pv = CvProfileVersion(
        id=str(uuid.uuid4()), cv_file_id=cv_file.id, user_id=user.id, version_number=1,
        profile_hash=uuid.uuid4().hex, schema_version="1.0",
        structured_payload={"basics": {"name": "Jane Doe"}},
    )
    session.add(pv)
    await session.flush()
    return pv


async def _job_post_profile(session, user):
    job_post = JobPost(
        id=str(uuid.uuid4()), user_id=user.id, source_type="text",
        raw_text="Python engineer wanted" * 10, status="structured",
    )
    session.add(job_post)
    await session.flush()
    jp_profile = JobPostProfile(
        id=str(uuid.uuid4()), job_post_id=job_post.id, job_title="Engineer", employer="Acme",
        required_skills=["Python"], preferred_skills=[],
    )
    session.add(jp_profile)
    await session.flush()
    return jp_profile


async def _workflow(session, user, *, status="awaiting_answers", current_step=1):
    pv = await _cv_profile_version(session, user)
    jp = await _job_post_profile(session, user)
    wf = CoverLetterWorkflow(
        id=str(uuid.uuid4()), user_id=user.id, cv_profile_version_id=pv.id,
        job_post_profile_id=jp.id, status=status, current_step=current_step, total_steps=3,
        question_set_version=1,
    )
    session.add(wf)
    await session.flush()
    return wf


async def _question(session, wf, *, step_number=1, required=True, category="employer_interest"):
    q = CoverLetterQuestion(
        id=str(uuid.uuid4()), workflow_id=wf.id, step_number=step_number,
        question_text="Why this role?", question_category=category, required=required,
    )
    session.add(q)
    await session.flush()
    return q


async def _draft(session, wf, *, status="generated", version_number=1):
    d = CoverLetterDraft(
        id=str(uuid.uuid4()), workflow_id=wf.id, version_number=version_number,
        status=status, body_text="Dear Hiring Manager, ..." if status != "pending" else "",
    )
    session.add(d)
    await session.flush()
    return d


# ═══════════════════════════════════════════════════════════════════════
# GET /cover-letters — list
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_list_workflows_returns_job_title_and_employer_from_the_joined_profile():
    async with _test_session_factory() as s:
        user = await _user(s, "listcl1")
        await _workflow(s, user)
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_cover_letter_workflows(limit=20, offset=0, current_user=user, session=s)

    assert result.total == 1
    item = result.items[0]
    assert item.job_title == "Engineer"
    assert item.employer == "Acme"
    assert item.status == "awaiting_answers"
    assert item.current_step == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_list_workflows_only_returns_the_current_users_own_workflows():
    async with _test_session_factory() as s:
        user_a = await _user(s, "listclA")
        user_b = await _user(s, "listclB")
        await _workflow(s, user_a)
        await _workflow(s, user_b)
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_cover_letter_workflows(limit=20, offset=0, current_user=user_a, session=s)

    assert result.total == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_list_workflows_empty_for_a_user_with_no_workflows():
    async with _test_session_factory() as s:
        user = await _user(s, "listclempty")
        await s.commit()

    async with _test_session_factory() as s:
        result = await list_cover_letter_workflows(limit=20, offset=0, current_user=user, session=s)

    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio(loop_scope="function")
async def test_list_workflows_orders_most_recent_first_and_respects_pagination():
    async with _test_session_factory() as s:
        user = await _user(s, "listclpage")
        first = await _workflow(s, user)
        second = await _workflow(s, user)
        await s.commit()

    async with _test_session_factory() as s:
        page1 = await list_cover_letter_workflows(limit=1, offset=0, current_user=user, session=s)
        page2 = await list_cover_letter_workflows(limit=1, offset=1, current_user=user, session=s)

    assert page1.total == 2
    assert page1.items[0].id == second.id
    assert page2.items[0].id == first.id


# ═══════════════════════════════════════════════════════════════════════
# POST /cover-letters/{workflowId}/answers
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_final_step_dispatches_async_and_returns_generating():
    async with _test_session_factory() as s:
        user = await _user(s, "ans1")
        wf = await _workflow(s, user, status="awaiting_answers", current_step=3)
        q = await _question(s, wf, step_number=3, required=False)
        await s.commit()
        wf_id, q_id = wf.id, q.id

    async with _test_session_factory() as s:
        result = await submit_answers(
            workflowId=wf_id,
            body=SubmitAnswersRequest(answers=[AnswerItem(questionId=q_id, answerText="Answer text")]),
            current_user=user, session=s,
        )
        # Not "draft_ready" — generation now runs asynchronously.
        assert result.status == "generating"

    async with _test_session_factory() as verify_s:
        draft_result = await verify_s.execute(
            select(CoverLetterDraft).where(CoverLetterDraft.workflow_id == wf_id)
        )
        draft = draft_result.scalar_one()
        assert draft.status == "pending"

        job_result = await verify_s.execute(
            select(ProcessingJob).where(ProcessingJob.source_entity_id == draft.id)
        )
        job = job_result.scalar_one()
        assert job.job_type == "cover_letter_generate"
        assert job.source_entity_type == "cover_letter_draft"


@pytest.mark.asyncio(loop_scope="function")
async def test_required_question_left_unanswered_is_rejected():
    """09-test-plan.md §7: a required question left unanswered must be
    handled explicitly, not silently proceed as if answered — previously
    unenforced anywhere in this codebase."""
    async with _test_session_factory() as s:
        user = await _user(s, "ans2")
        wf = await _workflow(s, user, status="awaiting_answers", current_step=1)
        await _question(s, wf, step_number=1, required=True)
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await submit_answers(
                workflowId=wf_id, body=SubmitAnswersRequest(answers=[]),
                current_user=user, session=s,
            )
        assert exc.value.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_non_final_step_just_advances_no_dispatch():
    async with _test_session_factory() as s:
        user = await _user(s, "ans3")
        wf = await _workflow(s, user, status="awaiting_answers", current_step=1)
        q = await _question(s, wf, step_number=1, required=False)
        await s.commit()
        wf_id, q_id = wf.id, q.id

    async with _test_session_factory() as s:
        result = await submit_answers(
            workflowId=wf_id,
            body=SubmitAnswersRequest(answers=[AnswerItem(questionId=q_id, answerText="x")]),
            current_user=user, session=s,
        )
        assert result.status == "awaiting_answers"
        assert result.current_step == 2

    async with _test_session_factory() as verify_s:
        draft_result = await verify_s.execute(
            select(CoverLetterDraft).where(CoverLetterDraft.workflow_id == wf_id)
        )
        assert draft_result.scalar_one_or_none() is None


# ═══════════════════════════════════════════════════════════════════════
# GET /cover-letters/{workflowId}/draft
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_get_draft_404s_while_pending():
    """Direct regression test for the bug this async restructuring
    introduces if unfixed: a draft row now exists immediately on submit,
    before the worker has generated anything — draft is None is no
    longer sufficient."""
    async with _test_session_factory() as s:
        user = await _user(s, "get1")
        wf = await _workflow(s, user, status="generating")
        await _draft(s, wf, status="pending")
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await get_draft(workflowId=wf_id, current_user=user, session=s)
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_get_draft_returns_generated_draft():
    async with _test_session_factory() as s:
        user = await _user(s, "get2")
        wf = await _workflow(s, user, status="draft_ready")
        await _draft(s, wf, status="generated")
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        response = await get_draft(workflowId=wf_id, current_user=user, session=s)
        assert response.status == "generated"


@pytest.mark.asyncio(loop_scope="function")
async def test_get_draft_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "get3owner")
        wf = await _workflow(s, owner, status="draft_ready")
        await _draft(s, wf, status="generated")
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "get3attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await get_draft(workflowId=wf_id, current_user=attacker, session=s)
        assert exc.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# POST /cover-letters/{workflowId}/regenerate
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_regenerate_creates_new_pending_version():
    async with _test_session_factory() as s:
        user = await _user(s, "regen1")
        wf = await _workflow(s, user, status="draft_ready")
        await _draft(s, wf, status="generated", version_number=1)
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        result = await regenerate(request=_fake_request(), workflowId=wf_id, current_user=user, session=s)
        assert result.status == "queued"

    async with _test_session_factory() as verify_s:
        drafts = (await verify_s.execute(
            select(CoverLetterDraft).where(CoverLetterDraft.workflow_id == wf_id)
        )).scalars().all()
        assert len(drafts) == 2
        new_draft = next(d for d in drafts if d.version_number == 2)
        assert new_draft.status == "pending"


@pytest.mark.asyncio(loop_scope="function")
async def test_regenerate_accepts_generation_failed_state():
    """Direct regression test: without this, a genuinely failed
    generation would permanently lock the workflow out of retrying —
    the same 'dead end' class of bug as a queue with no consuming
    worker."""
    async with _test_session_factory() as s:
        user = await _user(s, "regen2")
        wf = await _workflow(s, user, status="generation_failed")
        await _draft(s, wf, status="failed", version_number=1)
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        result = await regenerate(request=_fake_request(), workflowId=wf_id, current_user=user, session=s)
        assert result.status == "queued"


@pytest.mark.asyncio(loop_scope="function")
async def test_regenerate_rejects_awaiting_answers_state():
    async with _test_session_factory() as s:
        user = await _user(s, "regen3")
        wf = await _workflow(s, user, status="awaiting_answers")
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await regenerate(request=_fake_request(), workflowId=wf_id, current_user=user, session=s)
        assert exc.value.status_code == 409


# ═══════════════════════════════════════════════════════════════════════
# DELETE /cover-letters/{workflowId}
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_sets_status_archived():
    async with _test_session_factory() as s:
        user = await _user(s, "del1")
        wf = await _workflow(s, user, status="draft_ready")
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        await delete_cover_letter_workflow(workflowId=wf_id, current_user=user, session=s)

    async with _test_session_factory() as verify_s:
        result = await verify_s.execute(select(CoverLetterWorkflow).where(CoverLetterWorkflow.id == wf_id))
        wf = result.scalar_one()
        assert wf.status == "archived"


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_rejects_wrong_owner():
    async with _test_session_factory() as s:
        owner = await _user(s, "del2owner")
        wf = await _workflow(s, owner)
        await s.commit()
        wf_id = wf.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "del2attacker")
        await s.commit()

    async with _test_session_factory() as s:
        with pytest.raises(HTTPException) as exc:
            await delete_cover_letter_workflow(workflowId=wf_id, current_user=attacker, session=s)
        assert exc.value.status_code == 404


@pytest.mark.asyncio(loop_scope="function")
async def test_archived_workflow_excluded_from_list():
    async with _test_session_factory() as s:
        user = await _user(s, "del3")
        wf_keep = await _workflow(s, user)
        wf_delete = await _workflow(s, user)
        await s.commit()
        wf_delete_id = wf_delete.id

    async with _test_session_factory() as s:
        await delete_cover_letter_workflow(workflowId=wf_delete_id, current_user=user, session=s)

    async with _test_session_factory() as s:
        result = await list_cover_letter_workflows(limit=20, offset=0, current_user=user, session=s)
        assert len(result.items) == 1
        assert result.items[0].id == wf_keep.id
