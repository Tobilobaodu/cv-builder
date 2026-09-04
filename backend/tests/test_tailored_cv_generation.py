"""Tests for tailored_cv_generation.py's orchestrator, against a fake LLM
client — no real API calls anywhere in this file. Covers the
09-test-plan.md §6 checklist directly: evidence traceability, empty-
section omission, fabrication resistance, contradictory/unclear
exclusion, and generation_task/prompt_version/model_id provenance.
"""
import json
from types import SimpleNamespace

from app.services.tailored_cv_generation import (
    generate_draft_sections,
    assemble_content_json,
    render_text_from_sections,
    build_validation_result,
    build_improvement_checklist,
    SECTION_SUMMARY,
    SECTION_EXPERIENCE,
    SECTION_SKILLS,
    SECTION_EDUCATION,
    SECTION_PROJECTS,
)


def _exp(id="exp1", title="Software Engineer", company="Acme Corp",
         bullets=None, technologies=None):
    return SimpleNamespace(
        id=id, title=title, company=company,
        bullets=bullets if bullets is not None else
        ["Built REST APIs serving 2M requests/day using Python and Docker"],
        technologies=technologies if technologies is not None else ["Python", "Docker"],
    )


def _skill(id="sk1", skill_name="Python"):
    return SimpleNamespace(id=id, skill_name=skill_name)


def _edu(id="ed1", institution="MIT", degree="BSc", field="Computer Science", year=2019):
    return SimpleNamespace(id=id, institution=institution, degree=degree, field=field, year=year)


def _cert(id="cert1", name="AWS Certified Solutions Architect", issuer="Amazon", year=2022):
    return SimpleNamespace(id=id, name=name, issuer=issuer, year=year)


def _project(id="proj1", name="Finance Tracker", description="A budgeting app",
             bullets=None, technologies=None):
    return SimpleNamespace(
        id=id, name=name, description=description,
        bullets=bullets if bullets is not None else ["Reduced manual entry by 80%"],
        technologies=technologies if technologies is not None else ["React", "Node"],
    )


def _evidence(support_level, requirement_text, requirement_type="required",
              suggestion=None, warning=None):
    return SimpleNamespace(
        support_level=support_level, requirement_text=requirement_text,
        requirement_type=requirement_type, suggestion=suggestion, warning=warning,
    )


class FakeCompletions:
    """Returns a fixed, minimal, generically-grounded response for every
    call by default (mentions only "Python" — present in every fixture in
    this file, so the default never accidentally fails verification
    against a specific test's evidence). Tests needing precise per-call
    behavior pass responses_by_schema, keyed by the schema_name
    generate_structured() sends — real calls include both a summary and
    an experience call in most tests here, so a flat, order-based queue
    would silently drain into whichever section runs first rather than
    the one the test actually means to target; keying by schema avoids
    that entirely.
    """

    def __init__(self, content_text="Experienced Python engineer.",
                 evidence_indexes=None, responses_by_schema=None):
        self.calls = []
        self._default_content_text = content_text
        self._default_evidence_indexes = evidence_indexes if evidence_indexes is not None else [0]
        self._queues = {k: list(v) for k, v in (responses_by_schema or {}).items()}

    def create(self, **kwargs):
        self.calls.append(kwargs)
        schema_name = kwargs["response_format"]["json_schema"]["name"]
        queue = self._queues.get(schema_name)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            content = json.dumps(item)
        else:
            content = json.dumps({
                "contentText": self._default_content_text,
                "evidenceIndexes": self._default_evidence_indexes,
            })
        message = SimpleNamespace(content=content, refusal=None)
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=50, completion_tokens=20)
        return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4o-mini-2024-07-18")


class FakeClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=FakeCompletions(**kwargs))


class TestGenerateDraftSections:

    def test_faithful_generation_produces_summary_experience_skills(self):
        exp = _exp()
        skill = _skill()
        evidence_items = [
            _evidence("supported", "Python"),
            _evidence("partially_supported", "REST APIs"),
        ]
        client = FakeClient()

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp],
            education_items=[],
            skill_items=[skill],
            job_requirements=["Python", "REST APIs"],
            llm_client_override=client,
        )

        section_types = [s.section_type for s in outcome.sections]
        assert SECTION_SUMMARY in section_types
        assert SECTION_EXPERIENCE in section_types
        assert SECTION_SKILLS in section_types
        assert outcome.issues == []

    def test_every_section_has_generation_task_prompt_version_model_id(self):
        """09-test-plan.md §6: 'Every tailored_cv_sections row ... has
        generation_task, prompt_version, and model_id populated ... from
        Phase 3 onward'."""
        exp = _exp()
        skill = _skill()
        evidence_items = [_evidence("supported", "Python")]
        client = FakeClient()

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp],
            education_items=[],
            skill_items=[skill],
            job_requirements=["Python"],
            llm_client_override=client,
        )

        assert len(outcome.sections) > 0
        for section in outcome.sections:
            assert section.generation_task, f"{section.section_type} missing generation_task"
            assert section.model_id, f"{section.section_type} missing model_id"
            # prompt_version is None only for the deterministic skills section
            if section.section_type != SECTION_SKILLS:
                assert section.prompt_version, f"{section.section_type} missing prompt_version"

    def test_no_evidence_omits_llm_sections_but_keeps_deterministic_ones(self):
        """With no match evidence, the LLM-bound summary is omitted (and the
        model is never called), but the deterministic skills and
        earlier-career listings still surface the candidate's real content
        — skills/roles must not silently vanish (cv-diagnosis.md)."""
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[],  # nothing to bind at all
            experience_items=[_exp()],
            education_items=[],
            skill_items=[_skill()],
            job_requirements=["Python"],
            llm_client_override=client,
        )
        section_types = [s.section_type for s in outcome.sections]
        assert SECTION_SUMMARY not in section_types
        assert SECTION_SKILLS in section_types
        assert SECTION_EXPERIENCE in section_types  # earlier-career line
        assert len(client.chat.completions.calls) == 0, "must not call the LLM with an empty evidence pool"
        assert any("no evidence available" in issue for issue in outcome.issues)

    def test_fabricated_claim_is_rejected_retried_then_omitted(self):
        """09-test-plan.md §6's 'tempt fabrication' case: every attempt
        returns a claim with a number never present in evidence — must be
        rejected every time and end up omitted, never persisted."""
        exp = _exp(bullets=["Built REST APIs serving 2M requests/day"], technologies=[])
        evidence_items = [_evidence("partially_supported", "REST APIs")]
        client = FakeClient(content_text="Handled 50M requests per day at massive scale.")

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp],
            education_items=[],
            skill_items=[],
            job_requirements=["REST APIs"],
            llm_client_override=client,
        )

        experience_sections = [s for s in outcome.sections if s.section_type == SECTION_EXPERIENCE]
        assert experience_sections == []
        assert any("failed verification" in issue for issue in outcome.issues)

    def test_corrective_retry_can_succeed_on_second_attempt(self):
        exp = _exp(bullets=["Built REST APIs serving 2M requests/day"], technologies=[])
        evidence_items = [_evidence("partially_supported", "REST APIs")]
        # Only the experience-bullet schema gets the reject-then-pass
        # queue — the summary call (also triggered by this evidence) uses
        # the generic default and succeeds trivially on its own.
        client = FakeClient(responses_by_schema={
            "tailored_cv_experience_bullet": [
                {"contentText": "Handled 50M requests per day.", "evidenceIndexes": [0]},  # rejected
                {"contentText": "Built REST APIs handling 2M requests per day.", "evidenceIndexes": [0]},  # passes
            ],
        })

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp],
            education_items=[],
            skill_items=[],
            job_requirements=["REST APIs"],
            llm_client_override=client,
        )

        experience_sections = [s for s in outcome.sections if s.section_type == SECTION_EXPERIENCE]
        assert len(experience_sections) == 1
        assert "2M" in experience_sections[0].content_text

    def test_evidence_indexes_out_of_range_are_ignored_not_crashed(self):
        exp = _exp()
        evidence_items = [_evidence("supported", "Python")]
        client = FakeClient(evidence_indexes=[99])  # invalid index — pool only has 1 candidate

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp],
            education_items=[],
            skill_items=[],
            job_requirements=["Python"],
            llm_client_override=client,
        )
        # No valid cited candidates -> correction loop -> exhausted -> omitted
        experience_sections = [s for s in outcome.sections if s.section_type == SECTION_EXPERIENCE]
        assert experience_sections == []

    def test_skills_section_makes_no_llm_call(self):
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[_evidence("supported", "Python")],
            experience_items=[],
            education_items=[],
            skill_items=[_skill()],
            job_requirements=["Python"],
            llm_client_override=client,
        )
        skills_sections = [s for s in outcome.sections if s.section_type == SECTION_SKILLS]
        assert len(skills_sections) == 1
        assert skills_sections[0].model_id == "rules-based"
        assert skills_sections[0].prompt_version is None

    def test_skills_include_all_ordered_matched_first(self):
        """cv-diagnosis.md Failure 1: skills are included in full (matched
        first, then the rest) — not filtered down to the matched subset."""
        exp = _exp()
        skill_matched = _skill(id="sk1", skill_name="Python")
        skill_unmatched = _skill(id="sk2", skill_name="Figma")
        evidence_items = [_evidence("supported", "Python")]
        client = FakeClient()

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp],
            education_items=[],
            skill_items=[skill_matched, skill_unmatched],
            job_requirements=["Python"],
            llm_client_override=client,
        )
        skills_sections = [s for s in outcome.sections if s.section_type == SECTION_SKILLS]
        assert len(skills_sections) == 1
        content = skills_sections[0].content_text
        assert "Python" in content
        assert "Figma" in content
        assert content.index("Python") < content.index("Figma")

    def test_bullets_shape_generates_multiple_verified_bullets(self):
        """cv-diagnosis.md Failure 3: the experience schema returns a
        bullets array; each bullet is verified independently — a grounded
        bullet is kept, a fabricated one (invented number) is dropped."""
        exp = _exp(bullets=["Built REST APIs serving 2M requests/day using Python"], technologies=["Python"])
        evidence_items = [_evidence("supported", "Python")]
        client = FakeClient(responses_by_schema={
            "tailored_cv_experience_bullet": [
                {"bullets": [
                    {"text": "Built REST APIs handling 2M requests per day.", "evidenceIndexes": [0]},
                    {"text": "Scaled the service to 50M requests per day.", "evidenceIndexes": [0]},
                ]},
            ],
        })

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp],
            education_items=[],
            skill_items=[],
            job_requirements=["Python"],
            llm_client_override=client,
        )
        experience_sections = [
            s for s in outcome.sections
            if s.section_type == SECTION_EXPERIENCE and s.source_item_id is not None
        ]
        assert len(experience_sections) == 1
        content = experience_sections[0].content_text
        assert "2M" in content
        assert "50M" not in content

    def test_max_experience_items_cap_is_respected(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "tailored_cv_max_experience_items", 1)

        exp1 = _exp(id="exp1", bullets=["Built APIs"], technologies=["Python"])
        exp2 = _exp(id="exp2", title="Backend Dev", company="Other Co",
                    bullets=["Wrote services"], technologies=["Python"])
        evidence_items = [_evidence("supported", "Python")]
        client = FakeClient()

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[exp1, exp2],
            education_items=[],
            skill_items=[],
            job_requirements=["Python"],
            llm_client_override=client,
        )
        experience_sections = [
            s for s in outcome.sections
            if s.section_type == SECTION_EXPERIENCE and s.source_item_id is not None
        ]
        assert len(experience_sections) == 1
        # The dropped role is preserved as a condensed earlier-career line.
        earlier = [
            s for s in outcome.sections
            if s.section_type == SECTION_EXPERIENCE and s.source_item_id is None
        ]
        assert len(earlier) == 1
        assert "Backend Dev" in earlier[0].content_text


class TestEducationSection:
    """Direct tests of the product requirement: certifications/diplomas
    count as the same class of checkable qualifications evidence as a
    formal degree — a CV with a cert but no degree must not have its
    education-type section omitted. Deterministic, no LLM call."""

    def test_education_only_cv_produces_section(self):
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[],
            experience_items=[], education_items=[_edu()], skill_items=[],
            job_requirements=[], llm_client_override=client,
        )
        edu_sections = [s for s in outcome.sections if s.section_type == SECTION_EDUCATION]
        assert len(edu_sections) == 1
        assert edu_sections[0].model_id == "rules-based"
        assert edu_sections[0].prompt_version is None
        assert "BSc" in edu_sections[0].content_text

    def test_certification_only_cv_still_produces_education_section(self):
        """The direct test of the union gate: no formal degree, but a
        certification exists — the education-type section must NOT be
        omitted."""
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[],
            experience_items=[], education_items=[], skill_items=[],
            certification_items=[_cert()],
            job_requirements=[], llm_client_override=client,
        )
        edu_sections = [s for s in outcome.sections if s.section_type == SECTION_EDUCATION]
        assert len(edu_sections) == 1
        assert "AWS Certified Solutions Architect" in edu_sections[0].content_text
        assert len(client.chat.completions.calls) == 0, "education section must never call the LLM"

    def test_both_empty_omits_section(self):
        """Same silent-omission precedent as the skills section: no LLM
        call was attempted, so nothing 'failed' — no issue is logged."""
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[],
            experience_items=[], education_items=[], skill_items=[],
            job_requirements=[], llm_client_override=client,
        )
        edu_sections = [s for s in outcome.sections if s.section_type == SECTION_EDUCATION]
        assert edu_sections == []

    def test_projects_present_but_no_education_or_certifications_still_omits(self):
        """Direct regression test: projects must never satisfy the
        education gate — the union is education + certifications only."""
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[],
            experience_items=[], education_items=[], skill_items=[],
            project_items=[_project()],
            job_requirements=[], llm_client_override=client,
        )
        edu_sections = [s for s in outcome.sections if s.section_type == SECTION_EDUCATION]
        assert edu_sections == []

    def test_evidence_references_are_non_empty_when_present(self):
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[],
            experience_items=[], education_items=[_edu()], skill_items=[],
            certification_items=[_cert()],
            job_requirements=[], llm_client_override=client,
        )
        edu_sections = [s for s in outcome.sections if s.section_type == SECTION_EDUCATION]
        assert set(edu_sections[0].evidence_references) == {"ed1", "cert1"}


class TestProjectsSection:

    def test_faithful_project_rewrite_produces_section(self):
        proj = _project()
        evidence_items = [_evidence("supported", "React")]
        client = FakeClient(content_text="Built a React and Node budgeting app.")

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[], education_items=[], skill_items=[],
            project_items=[proj],
            job_requirements=["React"], llm_client_override=client,
        )
        proj_sections = [s for s in outcome.sections if s.section_type == SECTION_PROJECTS]
        assert len(proj_sections) == 1
        assert proj_sections[0].generation_task == "tailored_cv_project"
        assert proj_sections[0].prompt_version == "v2"

    def test_fabricated_project_claim_is_rejected_and_omitted(self):
        proj = _project(bullets=["Reduced manual entry by 80%"], technologies=[])
        evidence_items = [_evidence("partially_supported", "Finance Tracker")]
        client = FakeClient(content_text="Reduced manual entry by 95% for 10,000 users.")

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[], education_items=[], skill_items=[],
            project_items=[proj],
            job_requirements=["Finance Tracker"], llm_client_override=client,
        )
        proj_sections = [s for s in outcome.sections if s.section_type == SECTION_PROJECTS]
        assert proj_sections == []
        assert any("failed verification" in issue for issue in outcome.issues)

    def test_no_projects_means_no_llm_call_for_projects(self):
        client = FakeClient()
        outcome = generate_draft_sections(
            match_evidence_items=[],
            experience_items=[], education_items=[], skill_items=[],
            job_requirements=[], llm_client_override=client,
        )
        proj_sections = [s for s in outcome.sections if s.section_type == SECTION_PROJECTS]
        assert proj_sections == []

    def test_project_count_exceeding_cap_is_respected(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "tailored_cv_max_project_items", 1)

        proj1 = _project(id="p1", bullets=["Built API"], technologies=["Python"])
        proj2 = _project(id="p2", name="CLI Tool", bullets=["Wrote a CLI"], technologies=["Python"])
        evidence_items = [_evidence("supported", "Python")]
        client = FakeClient(content_text="Built API using Python.")

        outcome = generate_draft_sections(
            match_evidence_items=evidence_items,
            experience_items=[], education_items=[], skill_items=[],
            project_items=[proj1, proj2],
            job_requirements=["Python"], llm_client_override=client,
        )
        proj_sections = [s for s in outcome.sections if s.section_type == SECTION_PROJECTS]
        assert len(proj_sections) == 1

    def test_zero_relevance_project_is_still_attempted_not_excluded(self):
        """Direct regression test of the 'ranking, not gating' divergence:
        unlike experience, a project with zero job-match relevance must
        still get a generation attempt."""
        proj = _project(bullets=["Reduced manual entry by 80%"], technologies=[])
        client = FakeClient(content_text="Reduced manual entry by 80%.")

        outcome = generate_draft_sections(
            match_evidence_items=[],  # nothing to rank relevance from
            experience_items=[], education_items=[], skill_items=[],
            project_items=[proj],
            job_requirements=[], llm_client_override=client,
        )
        proj_sections = [s for s in outcome.sections if s.section_type == SECTION_PROJECTS]
        assert len(proj_sections) == 1


class TestAssembleAndRender:

    def test_content_json_and_render_text_are_ordered(self):
        from app.services.tailored_cv_generation import SectionResult
        sections = [
            SectionResult(SECTION_SKILLS, "Python", ["sk1"], "tailored_cv_skills", None, "rules-based", "passed", 1),
            SectionResult(SECTION_SUMMARY, "Summary text", ["exp1"], "tailored_cv_summary", "v1", "gpt-4o-mini", "passed", 0),
        ]
        content_json = assemble_content_json(sections)
        assert [s["sectionType"] for s in content_json["sections"]] == [SECTION_SUMMARY, SECTION_SKILLS]

        rendered = render_text_from_sections(sections)
        assert rendered.index("Summary text") < rendered.index("Python")

    def test_validation_result_passed_true_when_any_section_exists(self):
        from app.services.tailored_cv_generation import SectionResult, GenerationOutcome
        outcome = GenerationOutcome(
            sections=[SectionResult(SECTION_SKILLS, "Python", ["sk1"], "tailored_cv_skills", None, "rules-based", "passed", 0)],
            issues=["summary: no evidence available, section omitted"],
        )
        result = build_validation_result(outcome)
        assert result["passed"] is True
        assert result["issues"] == ["summary: no evidence available, section omitted"]

    def test_validation_result_passed_false_when_no_sections(self):
        from app.services.tailored_cv_generation import GenerationOutcome
        outcome = GenerationOutcome(sections=[], issues=["summary: no evidence available, section omitted"])
        assert build_validation_result(outcome)["passed"] is False


class TestImprovementChecklist:

    def test_excludes_supported_items(self):
        items = [_evidence("supported", "Python")]
        assert build_improvement_checklist(items) == []

    def test_contradictory_and_unclear_are_surfaced_here_not_generation(self):
        """This is their only path to visibility — 09-test-plan.md §6's
        'never silently resolved' requirement."""
        items = [
            _evidence("contradictory", "Team Lead", warning="Conflicting titles at AcmeCorp."),
            _evidence("unclear", "Cloud experience"),
        ]
        checklist = build_improvement_checklist(items)
        assert len(checklist) == 2
        support_levels = {c["supportLevel"] for c in checklist}
        assert support_levels == {"contradictory", "unclear"}

    def test_priority_high_for_required_unsupported(self):
        items = [_evidence("unsupported", "Kubernetes", requirement_type="required")]
        checklist = build_improvement_checklist(items)
        assert checklist[0]["priority"] == "high"

    def test_priority_low_for_preferred_partially_supported(self):
        items = [_evidence("partially_supported", "GraphQL", requirement_type="preferred")]
        checklist = build_improvement_checklist(items)
        assert checklist[0]["priority"] == "low"

    def test_uses_existing_suggestion_or_warning_before_template(self):
        items = [_evidence("unsupported", "Kubernetes", suggestion="Custom suggestion text")]
        checklist = build_improvement_checklist(items)
        assert checklist[0]["suggestion"] == "Custom suggestion text"

    def test_falls_back_to_template_when_no_suggestion_or_warning(self):
        items = [_evidence("unsupported", "Kubernetes")]
        checklist = build_improvement_checklist(items)
        assert "Kubernetes" in checklist[0]["suggestion"]
