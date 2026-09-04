"""Bare 'Experience'/'Employment'/'Work' heading canonicalization.

Every existing WORK_EXPERIENCE pattern requires a two-word phrase
("employment history", "work experience", etc.) — none matched a bare
standalone heading, which is common on real CVs. This left such CVs with
zero CvExperienceItem rows (no evidence for tailored-CV generation) and
let the section's content leak into the skills list instead.
"""

from app.extraction.heading_canonicalizer import canonicalize_heading, WORK_EXPERIENCE, UNKNOWN


def test_bare_experience_headings():
    for heading in ["Experience", "EXPERIENCE", "Employment", "Work"]:
        section, confidence = canonicalize_heading(heading)
        assert section == WORK_EXPERIENCE, f"{heading!r} -> {section}, expected {WORK_EXPERIENCE}"
        assert confidence >= 0.9, f"{heading!r} confidence {confidence} too low"


def test_qualified_experience_headings_still_work():
    for heading in ["Employment History", "Work Experience", "Career History"]:
        section, confidence = canonicalize_heading(heading)
        assert section == WORK_EXPERIENCE, f"{heading!r} -> {section}"


def test_ambiguous_experience_headings_stay_unknown():
    """Compound/ambiguous headings must NOT be swept in by the bare-word
    pattern — it's anchored to the full heading text specifically to
    avoid this."""
    assert canonicalize_heading("Voluntary Experience")[0] == UNKNOWN
    assert canonicalize_heading("Volunteer Experience")[0] == UNKNOWN


def test_bare_experience_does_not_collide_with_other_sections():
    assert canonicalize_heading("Experience")[0] != "skills"
    assert canonicalize_heading("Experience")[0] != "education"
