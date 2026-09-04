"""Metrics counter wiring tests — Workstream H.

An alert is only a real control if the counter it reads actually moves at the
chokepoint. These prove each of the 4 new attack-pattern counters increments
under its triggering condition. Reads current values via
prometheus_client.REGISTRY.get_sample_value and asserts the delta.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import RequestIdentity, hash_password
from app.api.v1.auth import login
from app.api.v1.matches import get_match
from app.schemas.auth import LoginRequest
from app.services.ssrf_safe_fetch import ssrf_safe_fetch, SSRFRejection
from app.extraction import evidence_binder
from app.services.generation_core import (
    GENERATION_JSON_SCHEMA, GenerationOutcome, generate_and_verify_section,
)
from app.db.models import CvFile, CvProfileVersion, JobPost, JobPostProfile, MatchRun, User

_test_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)


def _sample(name, labels=None):
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="198.51.100.7"), headers={})


async def _user(session, tag=""):
    u = User(
        id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}{tag}@test.example",
        password_hash=hash_password("ValidPassword123!"), status="active",
    )
    session.add(u)
    await session.flush()
    return u


def test_ssrf_rejected_counter_increments():
    before = _sample("ssrf_rejected_total")
    with pytest.raises(SSRFRejection):
        ssrf_safe_fetch("file:///etc/passwd")
    assert _sample("ssrf_rejected_total") - before >= 1


class _Completions:
    def create(self, **kwargs):
        message = SimpleNamespace(
            content=json.dumps({"contentText": "The candidate has 15 years of leadership.", "evidenceIndexes": [0]}),
            refusal=None,
        )
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=5, completion_tokens=5)
        return SimpleNamespace(choices=[choice], usage=usage, model="fake")


def test_generation_schema_validation_counter_increments():
    candidates = [evidence_binder.EvidenceCandidate("experience", "exp1", "Software engineer using Python")]
    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    before = _sample("generation_schema_validation_failed_total")
    outcome = GenerationOutcome()
    section = generate_and_verify_section(
        section_type="summary", system_prompt="s", generation_task="t", prompt_version="v1",
        schema=GENERATION_JSON_SCHEMA, schema_name="n", candidates=candidates,
        user_payload="DATA", order_index=0, outcome=outcome, llm_client_override=client,
    )
    assert section is None
    assert _sample("generation_schema_validation_failed_total") - before >= 1


@pytest.fixture(autouse=True)
def _reset_limiter():
    import app.core.rate_limit as rl
    rl._attempts.clear()
    rl._blocked.clear()
    yield
    rl._attempts.clear()
    rl._blocked.clear()


@pytest.mark.asyncio(loop_scope="function")
async def test_auth_failure_counter_increments_on_wrong_password():
    async with _test_session_factory() as s:
        user = await _user(s, "metricsauth")
        await s.commit()
        email = user.email

    before = _sample("auth_failures_total", {"reason": "wrong_password"})
    async with _test_session_factory() as s:
        with pytest.raises(Exception):
            await login(
                body=LoginRequest(email=email, password="WrongPassword123!"),
                request=_request(), session=s,
            )
    assert _sample("auth_failures_total", {"reason": "wrong_password"}) - before >= 1


@pytest.mark.asyncio(loop_scope="function")
async def test_authz_denied_counter_increments_on_cross_user_access():
    async with _test_session_factory() as s:
        owner = await _user(s, "ownermetr")
        cv = CvFile(id=str(uuid.uuid4()), user_id=owner.id, filename="c.pdf",
                    mime_type="application/pdf", file_size=1, storage_key=str(uuid.uuid4()), status="parsed")
        s.add(cv)
        await s.flush()
        pv = CvProfileVersion(id=str(uuid.uuid4()), cv_file_id=cv.id, user_id=owner.id,
                              version_number=1, profile_hash=uuid.uuid4().hex,
                              schema_version="1.0", structured_payload={"basics": {}})
        s.add(pv)
        await s.flush()
        jp = JobPost(id=str(uuid.uuid4()), user_id=owner.id, source_type="text",
                     raw_text="x" * 20, status="structured")
        s.add(jp)
        await s.flush()
        jpp = JobPostProfile(id=str(uuid.uuid4()), job_post_id=jp.id, required_skills=["Python"])
        s.add(jpp)
        await s.flush()
        match = MatchRun(id=str(uuid.uuid4()), user_id=owner.id, cv_profile_version_id=pv.id,
                         job_post_profile_id=jpp.id, status="completed", score=0.5)
        s.add(match)
        await s.commit()
        match_id = match.id

    async with _test_session_factory() as s:
        attacker = await _user(s, "attackermetr")
        await s.commit()

    before = _sample("authz_denied_total")
    async with _test_session_factory() as s:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await get_match(matchId=match_id, identity=RequestIdentity(user=attacker, trial_session=None), session=s)
        assert exc.value.status_code == 404
    assert _sample("authz_denied_total") - before >= 1

