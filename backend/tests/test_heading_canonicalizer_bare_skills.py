"""Focused tests for Bug #5 — bare Skills heading canonicalization.

Tests that bare "Skills", "Skill", and "SKILLS" headings correctly map
to the skills canonical section, and that existing qualified headings
continue to work.
"""

from app.extraction.heading_canonicalizer import canonicalize_heading, SKILLS, UNKNOWN


def test_bare_skills():
    """Skills, Skill, SKILLS should all map to skills."""
    for heading in ["Skills", "Skill", "SKILLS"]:
        section, confidence = canonicalize_heading(heading)
        assert section == SKILLS, f"{heading!r} → {section}, expected {SKILLS}"
        assert confidence >= 0.5, f"{heading!r} confidence {confidence} < 0.5"


def test_qualified_skills_still_work():
    """Existing qualified headings still map correctly."""
    for heading in ["Technical Skills", "Key Skills", "Core Competencies"]:
        section, confidence = canonicalize_heading(heading)
        assert section == SKILLS, f"{heading!r} → {section}, expected {SKILLS}"
        assert confidence >= 0.5, f"{heading!r} confidence {confidence} < 0.5"


def test_collision_safety():
    """Bare skills match should NOT collide with other canonical sections."""
    assert canonicalize_heading("Certifications")[0] != SKILLS
    assert canonicalize_heading("Projects")[0] != SKILLS
    assert canonicalize_heading("Languages")[0] == UNKNOWN   # ambiguous


def test_not_skills():
    """Non-skills headings still map elsewhere."""
    section, _ = canonicalize_heading("Employment History")
    assert section != SKILLS, f"'Employment History' should NOT be skills"