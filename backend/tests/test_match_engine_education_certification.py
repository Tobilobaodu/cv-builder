"""match_engine.py's education/certification wiring (Phase 2 extraction
extension). Before this change, _flatten_cv_text() never read the
education/certifications/projects keys at all, so a job's degree or
certification requirement could structurally never score above
unsupported regardless of CV content — this is the core regression test
proving that bug is fixed. Uses real run_match(), no mocks.
"""

from app.extraction.match_engine import run_match, SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED


def _cv(education=None, certifications=None, projects=None, skills=None):
    return {
        "basics": {"summary": ""},
        "workExperience": [],
        "education": education or [],
        "certifications": certifications or [],
        "projects": projects or [],
        "skills": {"technical": skills or [], "soft": []},
    }


def _jp(qualifications=None, required_skills=None):
    return {
        "required_skills": required_skills or [],
        "preferred_skills": [],
        "qualifications": qualifications or [],
        "keywords": [],
    }


class TestEducationMatchesQualifications:

    def test_degree_requirement_matches_cv_education(self):
        """Before the fix this was structurally UNSUPPORTED — the CV
        payload's education content never reached the matching blob at
        all."""
        cv = _cv(education=[{"institution": "University of Leeds", "degree": "BSc",
                              "field": "Computer Science", "year": 2019}])
        jp = _jp(qualifications=["BSc Computer Science"])
        result = run_match(cv, [], jp)
        assert result.evidence_items[0].support_level != UNSUPPORTED

    def test_no_education_stays_unsupported(self):
        cv = _cv()
        jp = _jp(qualifications=["Bachelor's degree in Computer Science"])
        result = run_match(cv, [], jp)
        assert result.evidence_items[0].support_level == UNSUPPORTED


class TestCertificationExactMatch:

    def test_exact_certification_name_is_supported(self):
        cv = _cv(certifications=[{"name": "AWS Certified Solutions Architect", "issuer": "AWS", "year": 2022}])
        jp = _jp(qualifications=["AWS Certified Solutions Architect"])
        result = run_match(cv, [], jp)
        item = result.evidence_items[0]
        assert item.support_level == SUPPORTED
        assert item.confidence == 0.85
        assert item.source_references == ["certification:AWS Certified Solutions Architect"]

    def test_fuzzy_certification_match_is_only_partially_supported(self):
        """Exact gets supported, near/fuzzy stays partially_supported —
        the tier boundary this feature calls for."""
        cv = _cv(certifications=[{"name": "AWS Certified Solutions Architect Associate", "issuer": "AWS"}])
        jp = _jp(qualifications=["AWS Certified Solutions Architect"])
        result = run_match(cv, [], jp)
        assert result.evidence_items[0].support_level == PARTIALLY_SUPPORTED

    def test_no_certification_stays_unsupported(self):
        cv = _cv()
        jp = _jp(qualifications=["PMP Certification"])
        result = run_match(cv, [], jp)
        assert result.evidence_items[0].support_level == UNSUPPORTED


class TestProjectsFeedGeneralMatching:

    def test_project_technology_supports_a_skill_requirement(self):
        """Projects are general technical evidence, not a qualifications
        claim, but they still flow into the shared matching blob — a
        requirement matching a project's tech stack resolves above
        unsupported even with no formal skills/education/certifications."""
        cv = _cv(projects=[{"name": "Finance Tracker", "description": "A budgeting app",
                             "bullets": [], "technologies": ["Kubernetes"]}])
        jp = _jp(qualifications=["Kubernetes"])
        result = run_match(cv, [], jp)
        assert result.evidence_items[0].support_level != UNSUPPORTED


class TestBackwardCompatibility:

    def test_payload_missing_new_keys_entirely_does_not_raise(self):
        """A payload from before this change (no education/certifications/
        projects keys at all) must behave identically to today — every
        new read uses .get(key, []) or []."""
        cv = {
            "basics": {"summary": "Experienced engineer"},
            "workExperience": [],
            "skills": {"technical": ["Python"], "soft": []},
        }
        jp = _jp(required_skills=["Python"], qualifications=["Bachelor's degree"])
        result = run_match(cv, ["Python"], jp)
        assert result.total_requirements == 2
        python_item = next(e for e in result.evidence_items if e.requirement_text == "Python")
        assert python_item.support_level == SUPPORTED
        degree_item = next(e for e in result.evidence_items if e.requirement_text == "Bachelor's degree")
        assert degree_item.support_level == UNSUPPORTED
