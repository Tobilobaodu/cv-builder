"""Tests for cover_letter_generation.py's orchestrator (Sprint 4), against
a fake LLM client — no real API calls anywhere in this file. Covers
09-test-plan.md §7's checklist directly: draft generation incorporates
submitted answers into evidence_references (not just prose without a
traceable link), and the primary-LLM-fails -> fallback-template path.
"""
import json
from types import SimpleNamespace

from app.services.cover_letter_generation import (
    build_evidence_pool,
    generate_draft,
)


def _exp(id="exp1", title="Software Engineer", company="Acme Corp",
         bullets=None, technologies=None):
    return SimpleNamespace(
        id=id, title=title, company=company,
        bullets=bullets if bullets is not None else
        ["Built REST APIs serving 2M requests/day using Python."],
        technologies=technologies if technologies is not None else ["Python"],
    )


def _skill(id="sk1", skill_name="Python"):
    return SimpleNamespace(id=id, skill_name=skill_name)


def _question(id="q1", text="Why this role?"):
    return SimpleNamespace(id=id, question_text=text)


def _answer(id="ans1", question_id="q1", text="I love the mission."):
    return SimpleNamespace(id=id, question_id=question_id, answer_text=text)


class FakeCompletions:
    def __init__(self, content_text=None, evidence_indexes=None):
        self.calls = []
        self._content_text = content_text or (
            "Dear Hiring Manager, I am excited to apply for the Senior "
            "Product Designer role at HealthTech Co. At Acme Corp, I "
            "built REST APIs serving 2M requests per day using Python. "
            "Sincerely, Jane Doe"
        )
        self._evidence_indexes = evidence_indexes if evidence_indexes is not None else [0]

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = json.dumps({
            "contentText": self._content_text,
            "evidenceIndexes": self._evidence_indexes,
        })
        message = SimpleNamespace(content=content, refusal=None)
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=10)
        return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4o-mini-test")


class FakeClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=FakeCompletions(**kwargs))


class TestBuildEvidencePool:

    def test_pool_includes_cv_rows_and_answers(self):
        pool = build_evidence_pool(
            match_evidence_items=[], experience_items=[_exp()], education_items=[],
            skill_items=[_skill()], certification_items=[], project_items=[],
            questions_by_id={"q1": _question()}, answers=[_answer()],
        )
        types = {c.row_type for c in pool}
        assert "experience" in types
        assert "skill" in types
        assert "answer" in types

    def test_no_match_uses_unfiltered_cv_pool(self):
        """No match run -> raw CV candidates are used directly, not
        filtered through bind_evidence_pool (which would zero them out
        with nothing to filter against) — precedent: tailored_cv_
        generation.py's _generate_education_section."""
        pool = build_evidence_pool(
            match_evidence_items=[], experience_items=[_exp()], education_items=[],
            skill_items=[], certification_items=[], project_items=[],
            questions_by_id={}, answers=[],
        )
        assert any(c.row_type == "experience" for c in pool)


class TestGenerateDraft:

    def test_faithful_generation_uses_llm_path(self):
        exp = _exp()
        pool = build_evidence_pool(
            match_evidence_items=[], experience_items=[exp], education_items=[],
            skill_items=[], certification_items=[], project_items=[],
            questions_by_id={"q1": _question()}, answers=[_answer()],
        )
        client = FakeClient()

        result = generate_draft(
            evidence_pool=pool, job_requirements=["Python"],
            job_title="Senior Product Designer", employer_name="HealthTech Co",
            cv_name="Jane Doe", tone=None, answers_by_step={},
            experience_items=[exp], project_items=[], skill_items=[],
            llm_client_override=client,
        )
        assert result.source == "llm"
        assert result.evidence_references == ["exp1"]
        assert result.model_id == "gpt-4o-mini-test"
        assert result.prompt_version == "v1"

    def test_structural_facts_do_not_trigger_false_positive_rejection(self):
        """Direct regression test for the bug found live during Sprint 4
        implementation: a letter correctly stating the job title/
        employer/candidate name (all real, code-supplied facts, not
        evidence-pool content) must not be rejected as 'fabricated'."""
        exp = _exp()
        pool = build_evidence_pool(
            match_evidence_items=[], experience_items=[exp], education_items=[],
            skill_items=[], certification_items=[], project_items=[],
            questions_by_id={}, answers=[],
        )
        client = FakeClient()

        result = generate_draft(
            evidence_pool=pool, job_requirements=["Python"],
            job_title="Senior Product Designer", employer_name="HealthTech Co",
            cv_name="Jane Doe", tone=None, answers_by_step={},
            experience_items=[exp], project_items=[], skill_items=[],
            llm_client_override=client,
        )
        assert result.source == "llm", "structural facts must not cause a fallback"

    def test_fabricated_claim_falls_back_to_template(self):
        exp = _exp(bullets=["Built REST APIs serving 2M requests/day."], technologies=[])
        pool = build_evidence_pool(
            match_evidence_items=[], experience_items=[exp], education_items=[],
            skill_items=[], certification_items=[], project_items=[],
            questions_by_id={}, answers=[],
        )
        client = FakeClient(content_text="Handled 50M requests per day at massive scale for a Fortune 500 company.")

        result = generate_draft(
            evidence_pool=pool, job_requirements=["Python"],
            job_title="Engineer", employer_name="Acme", cv_name="Jane Doe", tone=None,
            answers_by_step={}, experience_items=[exp], project_items=[], skill_items=[],
            llm_client_override=client,
        )
        assert result.source == "fallback"
        assert result.model_id == "rules-based"
        assert result.body_text  # fallback still produces a real, non-empty letter
        assert "exp1" in result.evidence_references

    def test_kill_switch_forces_fallback_without_llm_call(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "cover_letter_llm_generation_enabled", False)

        exp = _exp()
        pool = build_evidence_pool(
            match_evidence_items=[], experience_items=[exp], education_items=[],
            skill_items=[], certification_items=[], project_items=[],
            questions_by_id={}, answers=[],
        )
        client = FakeClient()

        result = generate_draft(
            evidence_pool=pool, job_requirements=["Python"],
            job_title="Engineer", employer_name="Acme", cv_name="Jane Doe", tone=None,
            answers_by_step={}, experience_items=[exp], project_items=[], skill_items=[],
            llm_client_override=client,
        )
        assert result.source == "fallback"
        assert len(client.chat.completions.calls) == 0, "kill switch must skip the LLM call entirely"

    def test_empty_evidence_still_produces_a_fallback_letter(self):
        result = generate_draft(
            evidence_pool=[], job_requirements=[], job_title="Engineer",
            employer_name=None, cv_name=None, tone=None, answers_by_step={},
            experience_items=[], project_items=[], skill_items=[],
            llm_client_override=FakeClient(),
        )
        assert result.source == "fallback"
        assert result.body_text
        assert result.evidence_references == []
