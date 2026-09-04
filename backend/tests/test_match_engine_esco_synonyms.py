"""match_engine.py's ESCO synonym-match step (_match_requirement, step
1.5) — a requirement and a CV skill that are worded differently but
resolve to the same ESCO concept must be recognized as partially
supported, not unsupported. Uses the real, committed ESCO index (not a
mock) since this is specifically testing the wiring against real data.
"""

from app.extraction.match_engine import run_match, PARTIALLY_SUPPORTED


def test_synonym_terms_are_partially_supported():
    """'Usability testing' and 'usability engineering' are the same ESCO
    concept under different wording — confirmed present in the real
    index during M2 development."""
    cv = {
        "basics": {"summary": ""},
        "workExperience": [],
        "skills": {"technical": ["usability testing"], "soft": []},
    }
    jp = {
        "required_skills": [],
        "preferred_skills": [],
        "qualifications": ["usability engineering"],
        "keywords": [],
    }
    result = run_match(cv, ["usability testing"], jp)

    assert result.total_requirements == 1
    assert result.evidence_items[0].support_level == PARTIALLY_SUPPORTED
    assert "skill:usability testing" in (result.evidence_items[0].source_references or [])


def test_unrelated_terms_stay_unsupported():
    cv = {
        "basics": {"summary": ""},
        "workExperience": [],
        "skills": {"technical": ["cake decorating"], "soft": []},
    }
    jp = {
        "required_skills": [],
        "preferred_skills": [],
        "qualifications": ["nuclear reactor operation"],
        "keywords": [],
    }
    result = run_match(cv, ["cake decorating"], jp)

    assert result.evidence_items[0].support_level == "unsupported"
