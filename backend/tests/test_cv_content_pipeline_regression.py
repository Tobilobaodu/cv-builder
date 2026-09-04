"""Regression tests for the tailored-CV content-loss defect.

WHAT THIS REPRODUCES
--------------------
The Docling/Textract/merge/cv_parse pipeline was decommissioned and
replaced by a single LLM call (app/services/cv_analysis.py::analyze_cv,
run by worker_jobs.py::process_cv_analyze). That replacement extracts
only `basics` + `skills`; the old pipeline was the ONLY writer of
CvExperienceItem / CvEducationItem / CvCertificationItem / CvProjectItem
rows (verified by repo-wide grep: the only remaining constructor calls
live in decommissioned/step6_cv_parse_task.py and in test
fixtures).

Consequence, and the reason a real user's "tailored CV" is unusable:
worker_jobs.py::process_cv_generate loads those four row types by
cv_profile_version_id, gets four empty lists in production, and
generate_draft_sections therefore emits only a Summary and a Skills
list - no work history, no education. The same empty pool also starves
cover-letter generation.

Nothing caught this because no existing test exercises
extraction -> generation as one chain: test_cv_analysis.py stops at the
analysis dataclass, test_tailored_cv_generation.py hand-builds the very
experience/education rows production never creates, and the live tests
(test_e2e_match_v2.py / test_cv_analyze_and_match_live.py) seed profile
rows directly and stop at the match.

These tests assert the CONTRACT the fix must satisfy: whatever the CV
extraction step returns must carry enough structured content to produce
a CV that actually contains the candidate's roles and qualifications.
They are expected to FAIL until that extraction is restored.

Scope note: this file is deliberately pure - fake LLM client, no DB, no
network, mirroring test_cv_analysis.py's FakeCompletions/FakeClient
pattern. The *persistence* half (that the profile shim writes those rows
so process_cv_generate can read them back) requires a live Postgres and
belongs in test_cv_analyze_and_match_live.py; the signature guard at the
bottom of this file is a lightweight, explicitly-labelled stand-in.
"""
import json
from types import SimpleNamespace

from app.services.cv_analysis import analyze_cv
from app.services.tailored_cv_generation import (
    SECTION_EDUCATION,
    SECTION_EXPERIENCE,
    SECTION_SKILLS,
    SECTION_SUMMARY,
    generate_draft_sections,
)

# A real designer CV, close to the reproduction case in
# "CV matching fix/cv-diagnosis.md" (7 years, OSB Group, quantified
# achievements, certificates rather than a single classic degree).
CV_TEXT = """TOBILOBA ODU
oduoluwatobi@gmail.com | +447562695548 | tobilobaodu.com

Product Designer with seven years of experience across UX research, UI
design, design systems and conversion-focused digital optimisation.

EXPERIENCE
UX Design Manager, OSB Group (April 2022 - Present)
- Hands on design lead across seven savings and lending brands.
- Ran a heuristic audit that identified 43 navigation and conversion
  issues, validating the simplified journey with 20 UK participants.
- Built a design system with tokens and a component library.

Product Designer, Charter Savings Bank (June 2019 - March 2022)
- Improved output quality by 50% by introducing prototype testing.
- Increased user satisfaction by 25% across five products.

EDUCATION
BSc Computer Science, University of Lagos, 2016

CERTIFICATIONS
Google UX Design Certificate - Coursera, 2021

SKILLS
Figma, UX research, usability testing, design systems, component
libraries, WCAG 2.1, CRO, Hotjar, UserTesting, HTML, CSS, JavaScript
"""


def _analysis_payload(**overrides):
    """The shape the extraction step must return for a usable CV.

    `basics`/`skills`/scores/issues/tips mirror the current schema
    exactly; `experience`/`education`/`certifications`/`projects` are the
    structured content the decommissioned parser used to provide and
    which the tailored CV cannot be built without.
    """
    payload = {
        "overallScore": 74.0,
        "skillsetScore": 78.0,
        "formattingScore": 70.0,
        "atsIssues": [
            {"passed": True, "severity": "low", "title": "Contact details",
             "detail": "Name, email and phone all present."},
        ],
        "formattingIssues": [
            {"passed": True, "severity": "low", "title": "Bullet points",
             "detail": "Experience uses bullet points."},
        ],
        "tips": ["Lead each bullet with the measured outcome."],
        "basics": {
            "name": "Tobiloba Odu",
            "email": "oduoluwatobi@gmail.com",
            "phone": "+447562695548",
        },
        "skills": [
            "Figma", "UX research", "usability testing", "design systems",
            "component libraries", "WCAG 2.1", "CRO", "Hotjar",
            "UserTesting", "HTML", "CSS", "JavaScript",
        ],
        "experience": [
            {
                "title": "UX Design Manager",
                "company": "OSB Group",
                "startDate": "2022-04",
                "endDate": None,
                "current": True,
                "bullets": [
                    "Hands on design lead across seven savings and lending brands.",
                    "Ran a heuristic audit that identified 43 navigation and "
                    "conversion issues, validating the simplified journey with "
                    "20 UK participants.",
                    "Built a design system with tokens and a component library.",
                ],
                "technologies": ["Figma", "Hotjar", "UserTesting"],
            },
            {
                "title": "Product Designer",
                "company": "Charter Savings Bank",
                "startDate": "2019-06",
                "endDate": "2022-03",
                "current": False,
                "bullets": [
                    "Improved output quality by 50% by introducing prototype testing.",
                    "Increased user satisfaction by 25% across five products.",
                ],
                "technologies": ["Figma", "CRO"],
            },
        ],
        "education": [
            {
                "institution": "University of Lagos",
                "degree": "BSc",
                "field": "Computer Science",
                "year": 2016,
            },
        ],
        "certifications": [
            {"name": "Google UX Design Certificate", "issuer": "Coursera", "year": 2021},
        ],
        "projects": [],
    }
    payload.update(overrides)
    return payload



class FakeCompletions:
    """Verbatim the pattern in test_cv_analysis.py."""

    def __init__(self, payload=None, prompt_tokens=100, completion_tokens=200):
        self.calls = []
        self._payload = payload if payload is not None else _analysis_payload()
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self._payload), refusal=None)
        usage = SimpleNamespace(
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
            model="gpt-4o-mini-test",
        )


class FakeClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=FakeCompletions(**kwargs))


class GenerationFakeCompletions:
    """Fake for the generation half, keyed by schema name (mirroring
    test_tailored_cv_generation.py) so the experience call gets the
    structured-bullets shape its schema declares and the summary call
    gets the single-string shape.
    """

    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        schema_name = kwargs["response_format"]["json_schema"]["name"]
        if schema_name == "tailored_cv_experience_bullet":
            data = {
                "bullets": [
                    {
                        "text": "Led design across seven savings and lending brands.",
                        "evidenceIndexes": [0],
                    },
                ]
            }
        else:
            data = {
                "contentText": "Product designer with seven years of experience.",
                "evidenceIndexes": [0],
            }
        message = SimpleNamespace(content=json.dumps(data), refusal=None)
        usage = SimpleNamespace(prompt_tokens=50, completion_tokens=20)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=usage,
            model="gpt-4o-mini-test",
        )


class GenerationFakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=GenerationFakeCompletions())



def _rows_from_analysis(result):
    """Adapt the analysis result into the row-shaped objects the
    generation orchestrator consumes (the same attribute surface
    evidence_binder reads: id/title/company/bullets/technologies for
    experience, id/institution/degree/field for education, id/name/issuer
    for certifications).

    Production needs the equivalent mapping plus persistence in
    worker_jobs.py::_write_cv_profile_shim; that DB-backed half belongs in
    test_cv_analyze_and_match_live.py, not here. Doing the mapping locally
    keeps this test on the question that matters first: does the
    extraction step return enough content to build a real CV at all?
    """
    experience = [
        SimpleNamespace(
            id=f"exp{i}",
            title=e.get("title"),
            company=e.get("company"),
            bullets=list(e.get("bullets") or []),
            technologies=list(e.get("technologies") or []),
        )
        for i, e in enumerate(getattr(result, "experience", None) or [])
    ]
    education = [
        SimpleNamespace(
            id=f"edu{i}",
            institution=e.get("institution"),
            degree=e.get("degree"),
            field=e.get("field"),
            year=e.get("year"),
        )
        for i, e in enumerate(getattr(result, "education", None) or [])
    ]
    certifications = [
        SimpleNamespace(
            id=f"cert{i}",
            name=c.get("name"),
            issuer=c.get("issuer"),
            year=c.get("year"),
        )
        for i, c in enumerate(getattr(result, "certifications", None) or [])
    ]
    skills = [
        SimpleNamespace(id=f"sk{i}", skill_name=name)
        for i, name in enumerate(result.skills)
    ]
    return experience, education, certifications, skills


def _evidence(support_level, requirement_text, requirement_type="required"):
    return SimpleNamespace(
        support_level=support_level,
        requirement_text=requirement_text,
        requirement_type=requirement_type,
        suggestion=None,
        warning=None,
    )



class TestExtractionReturnsStructuredContent:
    """The extraction step is the origin of the defect: it must return the
    candidate's roles and qualifications, not only basics + skills."""

    def test_experience_is_extracted_with_roles_and_bullets(self):
        result = analyze_cv(CV_TEXT, client=FakeClient())

        experience = getattr(result, "experience", None)
        assert experience, (
            "CV analysis returned no structured work experience. This is the "
            "root cause of the unusable tailored CV: process_cv_generate has "
            "no roles to generate experience sections from, so the output is "
            "a summary and a skills list with no work history."
        )
        assert len(experience) == 2
        titles = [e.get("title") for e in experience]
        assert "UX Design Manager" in titles
        assert any(e.get("company") == "OSB Group" for e in experience)
        first = next(e for e in experience if e.get("title") == "UX Design Manager")
        assert len(first.get("bullets") or []) == 3

    def test_quantified_achievements_survive_extraction(self):
        """The numbers are the most persuasive content on the CV. If they
        are lost here, no prompt downstream can recover them."""
        result = analyze_cv(CV_TEXT, client=FakeClient())
        all_bullets = " ".join(
            b
            for e in (getattr(result, "experience", None) or [])
            for b in (e.get("bullets") or [])
        )
        for figure in ("43", "20", "50%", "25%"):
            assert figure in all_bullets, f"lost the {figure!r} figure in extraction"

    def test_education_and_certifications_are_extracted(self):
        result = analyze_cv(CV_TEXT, client=FakeClient())

        education = getattr(result, "education", None)
        certifications = getattr(result, "certifications", None)
        assert education, (
            "CV analysis returned no education. A CV shipped without the "
            "candidate's degree is wrong, not cautious."
        )
        assert education[0].get("institution") == "University of Lagos"
        assert certifications, "CV analysis returned no certifications."
        assert certifications[0].get("name") == "Google UX Design Certificate"

    def test_basics_and_skills_still_work(self):
        """Guard against the fix regressing what already works."""
        result = analyze_cv(CV_TEXT, client=FakeClient())
        assert result.basics["name"] == "Tobiloba Odu"
        assert "Figma" in result.skills



class TestExtractionFeedsAUsableTailoredCv:
    """End of the chain: extraction output must produce a CV containing
    experience and education, not just a summary and a skills list."""

    def test_generated_draft_contains_experience_and_education(self):
        result = analyze_cv(CV_TEXT, client=FakeClient())
        experience, education, certifications, skills = _rows_from_analysis(result)

        outcome = generate_draft_sections(
            match_evidence_items=[
                _evidence("supported", "Figma"),
                _evidence("supported", "design systems"),
                _evidence("partially_supported", "component libraries"),
            ],
            experience_items=experience,
            education_items=education,
            skill_items=skills,
            certification_items=certifications,
            project_items=[],
            job_requirements=["Figma", "design systems", "component libraries"],
            llm_client_override=GenerationFakeClient(),
        )

        section_types = [s.section_type for s in outcome.sections]
        assert SECTION_EXPERIENCE in section_types, (
            "The tailored CV has no experience section. Sections present: "
            f"{section_types}. This is exactly the reported defect - the "
            "document is not a usable CV."
        )
        assert SECTION_EDUCATION in section_types, (
            f"The tailored CV has no education section. Present: {section_types}"
        )
        assert SECTION_SUMMARY in section_types
        assert SECTION_SKILLS in section_types


class TestProfileShimPersistenceContract:
    """LIGHTWEIGHT SIGNATURE GUARD - static inspection, not a runtime
    behaviour test.

    The behavioural, DB-backed assertion (that process_cv_analyze writes
    CvExperienceItem/CvEducationItem/CvCertificationItem/CvProjectItem
    rows which process_cv_generate then reads back) requires a live
    Postgres and belongs in test_cv_analyze_and_match_live.py. This guard
    exists only so the persistence gap cannot be silently forgotten while
    the extraction half is fixed: restoring extraction without persisting
    it would leave the user-visible defect exactly as it is.
    """

    def test_shim_accepts_the_structured_rows_it_must_persist(self):
        import inspect

        from app.workers.worker_jobs import _write_cv_profile_shim

        params = set(inspect.signature(_write_cv_profile_shim).parameters)
        missing = {"experience", "education", "certifications", "projects"} - params
        assert not missing, (
            "_write_cv_profile_shim cannot persist "
            f"{sorted(missing)} - it only accepts {sorted(params)}. Until it "
            "does, the four CV row types stay empty in production and the "
            "tailored CV keeps shipping without work history or education, "
            "even once analyze_cv extracts them."
        )

