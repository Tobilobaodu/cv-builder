"""The match surface is widened beyond required_skills/preferred_skills to
include qualifications and keywords — otherwise any job post whose skill
content only shows up as qualification bullets (e.g. non-software-
engineering postings, which never populate required_skills) can never
produce a match, regardless of CV quality.
"""

from app.extraction.match_engine import run_match


def test_qualifications_and_keywords_are_included_in_matching():
    cv = {
        "basics": {"summary": "Product designer with Figma and UX research experience"},
        "workExperience": [],
        "skills": {"technical": ["Figma", "UX research"], "soft": []},
    }
    jp = {
        "required_skills": [],
        "preferred_skills": [],
        "qualifications": ["Figma", "UX research"],
        "keywords": ["accessibility"],
    }
    result = run_match(cv, ["Figma", "UX research"], jp)

    assert result.total_requirements == 3
    assert result.supported_count >= 1


def test_existing_required_preferred_only_case_is_unchanged():
    """Pinned regression: a fixture with no qualifications/keywords keys
    at all must produce the exact same score as before the widening."""
    cv = {
        "basics": {"summary": "Python dev"},
        "workExperience": [],
        "skills": {"technical": ["Python", "Docker"], "soft": []},
    }
    jp = {"required_skills": ["Python", "Docker"], "preferred_skills": []}
    result = run_match(cv, ["Python", "Docker"], jp)

    assert result.total_requirements == 2
    assert result.supported_count == 2
    assert result.score == 1.0


def test_qualifications_capped_at_15():
    cv = {"basics": {}, "workExperience": [], "skills": {"technical": [], "soft": []}}
    jp = {
        "required_skills": [],
        "preferred_skills": [],
        "qualifications": [f"skill-{i}" for i in range(20)],
        "keywords": [],
    }
    result = run_match(cv, [], jp)

    assert result.total_requirements == 15


def test_dedup_keeps_higher_weight_bucket():
    """The same term appearing in both required_skills (weight 1.0) and
    keywords (weight 0.5) must only be scored once, at the higher weight."""
    cv = {
        "basics": {"summary": "AI"},
        "workExperience": [],
        "skills": {"technical": ["AI"], "soft": []},
    }
    jp = {
        "required_skills": ["AI"],
        "preferred_skills": [],
        "qualifications": [],
        "keywords": ["AI"],
    }
    result = run_match(cv, ["AI"], jp)

    assert result.total_requirements == 1
    assert result.evidence_items[0].requirement_type == "required"


def test_responsibilities_are_not_scored():
    """responsibilities feed generation context elsewhere, but must never
    be treated as scored matching evidence here."""
    cv = {"basics": {}, "workExperience": [], "skills": {"technical": [], "soft": []}}
    jp = {
        "required_skills": [],
        "preferred_skills": [],
        "qualifications": [],
        "keywords": [],
        "responsibilities": ["Own major surfaces end-to-end"],
    }
    result = run_match(cv, [], jp)

    assert result.total_requirements == 0
