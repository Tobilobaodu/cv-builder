"""Tests for cover_letter.py's rewritten assemble_draft() (Sprint 4 fallback
layer) — pure functions, no DB, no LLM. Covers the reference-script-ported
logic (priority-requirement story selection, real evidence_references
instead of opaque tags, bounded length enforcement) and generate_questions()
(unchanged this sprint, kept green as a regression check).
"""
from types import SimpleNamespace

from app.core.config import settings
from app.services.cover_letter import (
    assemble_draft,
    generate_questions,
)


def _exp(id="exp1", title="Software Engineer", company="Acme Corp", bullets=None):
    return SimpleNamespace(
        id=id, title=title, company=company,
        bullets=bullets if bullets is not None else
        ["Built REST APIs serving 2M requests/day using Python and Docker."],
    )


def _project(id="proj1", name="Finance Tracker", description="A budgeting app", bullets=None, technologies=None):
    return SimpleNamespace(
        id=id, name=name, description=description,
        bullets=bullets if bullets is not None else ["Reduced manual entry by 80%."],
        technologies=technologies if technologies is not None else ["React"],
    )


def _skill(id="sk1", skill_name="Python"):
    return SimpleNamespace(id=id, skill_name=skill_name)


class TestAssembleDraft:

    def test_produces_six_part_structure(self):
        draft = assemble_draft(
            cv_name="Jane Doe", employer_name="HealthTech Co", job_title="Engineer",
            tone=None, answers_by_step={}, experience_items=[_exp()],
            project_items=[], skill_items=[], job_requirements=["Python"],
        )
        assert draft.body_text.startswith("Dear Hiring Manager,")
        assert "Sincerely,\nJane Doe" in draft.body_text
        assert "Engineer" in draft.body_text

    def test_evidence_references_are_real_ids_not_opaque_tags(self):
        draft = assemble_draft(
            cv_name="Jane Doe", employer_name="Acme", job_title="Engineer", tone=None,
            answers_by_step={1: [("a1", "I love this company.")]},
            experience_items=[_exp(id="exp1")], project_items=[], skill_items=[_skill(id="sk1")],
            job_requirements=["Python"],
        )
        assert "exp1" in draft.evidence_references
        assert "sk1" in draft.evidence_references
        assert "a1" in draft.evidence_references
        assert not any(":" in ref for ref in draft.evidence_references), (
            "must not contain the old opaque 'cv:summary'-style tags"
        )

    def test_story_selection_ranks_by_requirement_keyword_overlap(self):
        relevant = _exp(id="exp_relevant", bullets=["Built REST APIs using Python and Kubernetes."])
        irrelevant = _exp(id="exp_irrelevant", title="Barista", company="Cafe",
                           bullets=["Made coffee for customers."])
        draft = assemble_draft(
            cv_name=None, employer_name=None, job_title="Engineer", tone=None,
            answers_by_step={}, experience_items=[irrelevant, relevant],
            project_items=[], skill_items=[], job_requirements=["Kubernetes"],
        )
        assert "exp_relevant" in draft.evidence_references

    def test_story_count_capped_at_fallback_max_stories(self, monkeypatch):
        monkeypatch.setattr(settings, "cover_letter_fallback_max_stories", 1)
        exp1 = _exp(id="exp1", bullets=["Built things with Python."])
        exp2 = _exp(id="exp2", title="Dev", company="Beta", bullets=["Built other things with Python."])
        draft = assemble_draft(
            cv_name=None, employer_name=None, job_title="Engineer", tone=None,
            answers_by_step={}, experience_items=[exp1, exp2],
            project_items=[], skill_items=[], job_requirements=["Python"],
        )
        story_refs = [r for r in draft.evidence_references if r in ("exp1", "exp2")]
        assert len(story_refs) == 1

    def test_projects_are_valid_stories_too(self):
        draft = assemble_draft(
            cv_name=None, employer_name=None, job_title="Engineer", tone=None,
            answers_by_step={}, experience_items=[], project_items=[_project(id="proj1")],
            skill_items=[], job_requirements=["React"],
        )
        assert "proj1" in draft.evidence_references

    def test_no_email_phone_requirement_never_raises(self):
        """A reference script's validate_inputs() hard-required email/
        phone — this codebase's CV parser never populates either field
        (worker_jobs.py hardcodes them None), so that check is
        deliberately not ported; assemble_draft must never raise for
        missing contact details."""
        draft = assemble_draft(
            cv_name=None, employer_name=None, job_title="Engineer", tone=None,
            answers_by_step={}, experience_items=[], project_items=[], skill_items=[],
            job_requirements=[],
        )
        assert draft.body_text
        assert draft.evidence_references == []

    def test_over_max_length_trims_but_never_fabricates(self, monkeypatch):
        monkeypatch.setattr(settings, "cover_letter_max_word_count", 30)
        exp1 = _exp(id="exp1", bullets=["Built a thing that did something great for many users across the company."])
        exp2 = _exp(id="exp2", title="Dev", company="Beta",
                    bullets=["Shipped another thing that also did something great for a lot of people."])
        draft = assemble_draft(
            cv_name="X", employer_name="Y", job_title="Z", tone=None,
            answers_by_step={}, experience_items=[exp1, exp2], project_items=[],
            skill_items=[], job_requirements=["thing"],
        )
        # Trimmed down to 1 story (the lower-priority second story
        # dropped), never below 1 real story, never fabricated content.
        story_refs = [r for r in draft.evidence_references if r in ("exp1", "exp2")]
        assert len(story_refs) == 1

    def test_tone_parameter_does_not_crash_and_is_accepted(self):
        draft = assemble_draft(
            cv_name=None, employer_name=None, job_title="Engineer", tone="enthusiastic",
            answers_by_step={}, experience_items=[], project_items=[], skill_items=[],
            job_requirements=[],
        )
        assert draft.body_text


class TestGenerateQuestions:
    """Unchanged this sprint — kept green as a regression check only."""

    def test_produces_fixed_and_gap_questions(self):
        questions = generate_questions(
            cv_name="Jane", employer_name="Acme", job_title="Engineer",
            match_evidence=[
                {"id": "e1", "support_level": "unsupported", "requirement_text": "Kubernetes"},
            ],
        )
        categories = {q.question_category for q in questions}
        assert "employer_interest" in categories
        assert "clarification" in categories
