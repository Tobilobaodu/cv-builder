"""Option A fallback tests: single-call body generation when the structured
CV rows are missing.

Production reality (doc 17 §7): the decommissioned pipeline was the only
writer of CvExperienceItem/CvEducationItem/CvCertificationItem/
CvProjectItem rows, so worker_jobs.process_cv_generate reads four empty
lists and the row-driven per-section generators starve — the shipped draft
was a name, a contact line and a flat skills list.

The fallback under test: when the structured rows are missing but the full
CV + job text are available, generate_draft_sections() produces the whole
body with the same single-call rewrite (app/services/resume_rewrite.py)
that /try/upload already serves. Pure Python, fake rewrite, no DB, no
network — same discipline as test_tailored_cv_generation.py.
"""
from types import SimpleNamespace

import app.services.tailored_cv_generation as tt
from app.services.export_rendering import build_cv_docx_context
from app.services.resume_rewrite import ResumeRewriteError, ResumeRewriteResult
from app.services.tailored_cv_generation import (
    generate_draft_sections,
    SECTION_BODY,
    SECTION_EXPERIENCE,
    SECTION_SKILLS,
    SECTION_SUMMARY,
)

BODY_MARKDOWN = (
    "Rayo Odu\n"
    "\n"
    "## Professional Summary\n"
    "Business analyst with eight years across process mapping, requirements\n"
    "elicitation and reporting-focused delivery.\n"
    "\n"
    "## Experience\n"
    "### Business Support Officer — Birmingham City Council (Sep 2023 – Present)\n"
    "- Mapped as-is service processes using BPMN and workflow analysis.\n"
)


def _skill(id="sk1", skill_name="Python"):
    return SimpleNamespace(id=id, skill_name=skill_name)


def _exp(id="exp1", title="Software Engineer", company="Acme Corp",
         bullets=None, technologies=None):
    return SimpleNamespace(
        id=id, title=title, company=company,
        bullets=bullets if bullets is not None else
        ["Built REST APIs serving 2M requests/day using Python and Docker"],
        technologies=technologies if technologies is not None else ["Python", "Docker"],
    )


def _evidence(support_level, requirement_text, requirement_type="required"):
    return SimpleNamespace(
        support_level=support_level, requirement_text=requirement_text,
        requirement_type=requirement_type, suggestion=None, warning=None,
    )


def _install_fake_rewrite(monkeypatch, *, markdown=BODY_MARKDOWN,
                          information_needed=None, usage=None,
                          rewritten_experience=None, suggested_additions=None):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return ResumeRewriteResult(
            tailored_resume_markdown=markdown,
            information_needed=list(information_needed or []),
            usage=usage,
            rewritten_experience=list(rewritten_experience or []),
            suggested_additions=list(suggested_additions or []),
        )

    monkeypatch.setattr(tt, "rewrite_resume", fake)
    return calls


class _FakeCompletions:
    """Same contract as test_tailored_cv_generation.FakeCompletions: one
    fixed, generically-grounded response per call ("Python" appears in every
    fixture here, so evidence verification never rejects it)."""

    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(
            content='{"contentText": "Experienced Python engineer.", "evidenceIndexes": [0]}',
            refusal=None,
        )
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=50, completion_tokens=20)
        return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4o-mini-2024-07-18")


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())


class TestBodyFallbackWhenRowsAreMissing:

    def test_body_section_replaces_the_starving_row_driven_path(self, monkeypatch):
        calls = _install_fake_rewrite(monkeypatch)

        outcome = generate_draft_sections(
            match_evidence_items=[_evidence("supported", "Python")],
            experience_items=[], education_items=[], skill_items=[_skill()],
            job_requirements=["Python", "BPMN"],
            cv_text="FULL CV TEXT", job_post_text="FULL JOB TEXT",
            target_title="Business Analyst",
            cv_text_source_id="cvraw-1",
        )

        assert [s.section_type for s in outcome.sections] == [SECTION_BODY]
        body = outcome.sections[0]
        assert body.content_text == BODY_MARKDOWN
        assert body.generation_task == "tailored_cv_body"
        assert body.prompt_version == "v5"
        assert body.order_index == 0
        assert body.evidence_references == ["cvraw-1"]
        assert len(calls) == 1
        assert calls[0]["cv_text"] == "FULL CV TEXT"
        assert calls[0]["job_post_text"] == "FULL JOB TEXT"
        assert calls[0]["target_title"] == "Business Analyst"
        assert calls[0]["candidate_notes"] is None

    def test_flat_skills_list_is_not_duplicated_after_a_full_body(self, monkeypatch):
        _install_fake_rewrite(monkeypatch)

        outcome = generate_draft_sections(
            match_evidence_items=[_evidence("supported", "Python")],
            experience_items=[], education_items=[], skill_items=[_skill()],
            job_requirements=["Python"],
            cv_text="FULL CV TEXT", job_post_text="FULL JOB TEXT",
        )

        assert SECTION_SKILLS not in [s.section_type for s in outcome.sections]

    def test_information_needed_and_usage_fold_into_the_outcome(self, monkeypatch):
        _install_fake_rewrite(
            monkeypatch,
            information_needed=["Where are you based?"],
            usage={"prompt_tokens": 500, "completion_tokens": 200},
        )

        outcome = generate_draft_sections(
            match_evidence_items=[], experience_items=[], education_items=[],
            skill_items=[_skill()], job_requirements=["Python"],
            cv_text="CV", job_post_text="JOB",
        )

        assert "information needed: Where are you based?" in outcome.issues
        assert outcome.total_prompt_tokens == 500
        assert outcome.total_completion_tokens == 200

    def test_structured_fields_are_carried_onto_the_outcome(self, monkeypatch):
        experience = [{
            "role": "Business Support Officer", "company": "Birmingham City Council",
            "dates": "Sep 2023 - Present",
            "bullets": ["Mapped as-is service processes using BPMN and workflow analysis."],
        }]
        additions = ["Add a metric for the process-mapping work, if you have one."]
        _install_fake_rewrite(
            monkeypatch, rewritten_experience=experience, suggested_additions=additions,
        )

        outcome = generate_draft_sections(
            match_evidence_items=[], experience_items=[], education_items=[],
            skill_items=[_skill()], job_requirements=["Python"],
            cv_text="CV", job_post_text="JOB",
        )

        assert outcome.rewritten_experience == experience
        assert outcome.suggested_additions == additions

    def test_no_fallback_without_text_even_when_rows_are_empty(self, monkeypatch):
        calls = _install_fake_rewrite(monkeypatch)

        outcome = generate_draft_sections(
            match_evidence_items=[], experience_items=[], education_items=[],
            skill_items=[_skill()], job_requirements=["Python"],
            # no cv_text/job_post_text — the worker couldn't load them
        )

        assert calls == []
        assert SECTION_BODY not in [s.section_type for s in outcome.sections]
        assert SECTION_SKILLS in [s.section_type for s in outcome.sections]


class TestBodyFallbackDegradation:

    def test_rewrite_failure_falls_back_to_the_skills_section(self, monkeypatch):
        def boom(**kwargs):
            raise ResumeRewriteError("We couldn't finish the rewrite.")

        monkeypatch.setattr(tt, "rewrite_resume", boom)

        outcome = generate_draft_sections(
            match_evidence_items=[], experience_items=[], education_items=[],
            skill_items=[_skill()], job_requirements=["Python"],
            cv_text="CV", job_post_text="JOB",
        )

        assert [s.section_type for s in outcome.sections] == [SECTION_SKILLS]
        assert outcome.sections[0].generation_task == "tailored_cv_skills"
        assert any(issue.startswith("body:") for issue in outcome.issues)


class TestRowDrivenPathUnchanged:

    def test_rows_present_means_no_body_call_even_with_text(self, monkeypatch):
        # The post-fix pipeline (doc 16 Phase 2) persists real experience
        # rows; the fallback must stay off and the row-driven generators
        # must run exactly as before.
        def unexpected(**kwargs):
            raise AssertionError(
                "rewrite_resume must not run when experience rows exist"
            )

        monkeypatch.setattr(tt, "rewrite_resume", unexpected)

        outcome = generate_draft_sections(
            match_evidence_items=[
                _evidence("supported", "Python"),
                _evidence("partially_supported", "REST APIs"),
            ],
            experience_items=[_exp()],
            education_items=[],
            skill_items=[_skill()],
            job_requirements=["Python", "REST APIs"],
            llm_client_override=_FakeClient(),
            cv_text="FULL CV TEXT", job_post_text="FULL JOB TEXT",
        )

        types = [s.section_type for s in outcome.sections]
        assert SECTION_BODY not in types
        assert SECTION_SUMMARY in types
        assert SECTION_EXPERIENCE in types


class TestBodySectionRendering:

    def test_body_renders_line_by_line_not_one_collapsed_paragraph(self):
        section = SimpleNamespace(
            section_type="body",
            content_text="Rayo Odu\n\n## Experience\n- Built things",
            order_index=0, source_item_id=None,
        )

        context = build_cv_docx_context(
            candidate_name="Rayo Odu", contact_line="rayo@example.com | +44",
            sections=[section], experience_by_id={}, project_by_id={},
        )

        assert len(context["blocks"]) == 1
        block = context["blocks"][0]
        assert block["kind"] == "body"
        assert block["heading"] == "Tailored CV"
        assert block["paragraph"] is None
        assert block["lines"] == ["Rayo Odu", "## Experience", "- Built things"]
