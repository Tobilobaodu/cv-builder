"""Section-heading canonicalization for CV parsing.

Maps varied real-world heading text to a fixed, small set of canonical
section_type values per 03-data-model.md §3. Headings that don't confidently
match ANY canonical section return 'unknown' — never force-fit.

This module is Phase 2, Sprint 5 per 07b-phase2-sprint-tasks.md.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# ──────────────────────────────────────────────────────────────────────
# Canonical section types
# ──────────────────────────────────────────────────────────────────────

WORK_EXPERIENCE = "work_experience"
EDUCATION = "education"
SKILLS = "skills"
CERTIFICATIONS = "certifications"
PROJECTS = "projects"
SUMMARY = "summary"
UNKNOWN = "unknown"

CANONICAL_SECTIONS = frozenset({
    WORK_EXPERIENCE, EDUCATION, SKILLS, CERTIFICATIONS, PROJECTS, SUMMARY,
})

# ──────────────────────────────────────────────────────────────────────
# Mapping: canonical section → list of (pattern, is_regex) tuples
# ──────────────────────────────────────────────────────────────────────

# Each entry is (compiled_pattern_or_regex_string, is_regex: bool, canonical_type)
# String patterns use case-insensitive exact/substring matching.
# Regex patterns use re.search with re.I.

_SECTION_MAPPINGS: list[Tuple[re.Pattern, str]] = [
    # ── Work Experience ───────────────────────────────────────────
    (re.compile(r"employment\s+history", re.I), WORK_EXPERIENCE),
    (re.compile(r"professional\s+experience", re.I), WORK_EXPERIENCE),
    (re.compile(r"work\s+experience", re.I), WORK_EXPERIENCE),
    (re.compile(r"career\s+history", re.I), WORK_EXPERIENCE),
    (re.compile(r"employment\s+record", re.I), WORK_EXPERIENCE),
    (re.compile(r"professional\s+background", re.I), WORK_EXPERIENCE),
    (re.compile(r"relevant\s+experience", re.I), WORK_EXPERIENCE),
    (re.compile(r"work\s+history", re.I), WORK_EXPERIENCE),
    (re.compile(r"career\s+summary", re.I), WORK_EXPERIENCE),
    # Bare single-word heading — anchored to the FULL heading text (not a
    # \b-bounded substring match) so "Experience & Projects" or "Voluntary
    # Experience" don't accidentally qualify; only an exact standalone
    # "Experience"/"Employment"/"Work" heading matches.
    (re.compile(r"^(?:experience|employment|work)$", re.I), WORK_EXPERIENCE),

    # ── Education ─────────────────────────────────────────────────
    (re.compile(r"education", re.I), EDUCATION),
    (re.compile(r"academic\s+background", re.I), EDUCATION),
    (re.compile(r"qualifications", re.I), EDUCATION),
    (re.compile(r"academic\s+history", re.I), EDUCATION),
    (re.compile(r"educational\s+background", re.I), EDUCATION),
    (re.compile(r"academic\s+qualifications", re.I), EDUCATION),

    # ── Skills ────────────────────────────────────────────────────
    (re.compile(r"technical\s+skills", re.I), SKILLS),
    (re.compile(r"core\s+competenc", re.I), SKILLS),  # competencies / competency
    (re.compile(r"areas?\s+of\s+expertise", re.I), SKILLS),
    (re.compile(r"skills?\s*(?:&|and)?\s*(?:competenc|proficienc)", re.I), SKILLS),
    (re.compile(r"key\s+skills", re.I), SKILLS),
    (re.compile(r"professional\s+skills", re.I), SKILLS),
    (re.compile(r"skills?\s+summary", re.I), SKILLS),
    (re.compile(r"technical\s+proficienc", re.I), SKILLS),
    (re.compile(r"technology\s+stack", re.I), SKILLS),
    (re.compile(r"\bskills?\b", re.I), SKILLS),

    # ── Certifications ────────────────────────────────────────────
    (re.compile(r"certifications?", re.I), CERTIFICATIONS),
    (re.compile(r"licenses?", re.I), CERTIFICATIONS),
    (re.compile(r"professional\s+certifications?", re.I), CERTIFICATIONS),
    (re.compile(r"credentials", re.I), CERTIFICATIONS),
    (re.compile(r"accreditations?", re.I), CERTIFICATIONS),

    # ── Projects ──────────────────────────────────────────────────
    (re.compile(r"projects?", re.I), PROJECTS),
    (re.compile(r"key\s+projects?", re.I), PROJECTS),
    (re.compile(r"selected\s+projects?", re.I), PROJECTS),
    (re.compile(r"portfolio", re.I), PROJECTS),
    (re.compile(r"personal\s+projects?", re.I), PROJECTS),
    (re.compile(r"open\s+source", re.I), PROJECTS),

    # ── Summary / Profile ─────────────────────────────────────────
    (re.compile(r"professional\s+summary", re.I), SUMMARY),
    (re.compile(r"profile", re.I), SUMMARY),
    (re.compile(r"about(?:\s+me)?", re.I), SUMMARY),
    (re.compile(r"objective", re.I), SUMMARY),
    (re.compile(r"personal\s+statement", re.I), SUMMARY),
    (re.compile(r"career\s+objective", re.I), SUMMARY),
    (re.compile(r"executive\s+summary", re.I), SUMMARY),
]

# ──────────────────────────────────────────────────────────────────────
# Headings that should NEVER be force-fitted to a canonical section
# ──────────────────────────────────────────────────────────────────────

# These terms, when they appear as section headings, suggest the content
# is NOT a standard CV section. Mapping them to any canonical type would
# be a misclassification (and potentially a fabrication risk — e.g.
# "Voluntary Experience" → work_experience would let volunteer work
# masquerade as paid employment).

_AMBIGUOUS_HEADING_TERMS = [
    re.compile(r"voluntary?\s+experience", re.I),
    re.compile(r"volunteer(?:ing)?\s+(?:experience|work)", re.I),
    re.compile(r"awards?\s*(?:&|and)?\s*(?:recognition|honou?rs?)", re.I),
    re.compile(r"publications?", re.I),
    re.compile(r"conferences?", re.I),
    re.compile(r"languages?", re.I),
    re.compile(r"hobbies?\s*(?:&|and)?\s*interests?", re.I),
    re.compile(r"interests", re.I),
    re.compile(r"references?", re.I),
    re.compile(r"referees?", re.I),
    re.compile(r"memberships?", re.I),
    re.compile(r"affiliations?", re.I),
    re.compile(r"patents?", re.I),
    re.compile(r"speaking\s+engagements?", re.I),
    re.compile(r"military\s+service", re.I),
    re.compile(r"training", re.I),  # ambiguous — could be skills or courses
    re.compile(r"courses?", re.I),
    re.compile(r"workshops?", re.I),
]


def canonicalize_heading(heading_text: str) -> Tuple[str, float]:
    """Map a CV section heading to its canonical section_type.

    Args:
        heading_text: The raw heading text (e.g. "Employment History").

    Returns:
        A tuple of (canonical_section_type, confidence).
        canonical_section_type is one of the CANONICAL_SECTIONS or 'unknown'.
        confidence is 0.0–1.0 indicating how confident the match is.
    """
    cleaned = heading_text.strip()

    # Strip common trailing/leading artifacts
    cleaned = re.sub(r"^[\d]+[.)]\s*", "", cleaned)        # "1. Education"
    cleaned = re.sub(r"^\s*[-•*✦➤►]\s*", "", cleaned)      # "- Skills"
    cleaned = cleaned.strip()

    if not cleaned or len(cleaned) < 2:
        return UNKNOWN, 0.0

    # 1. Check ambiguous/known-non-canonical headings first.
    #    These ALWAYS return 'unknown' — they should never be force-fitted.
    for pattern in _AMBIGUOUS_HEADING_TERMS:
        if pattern.search(cleaned):
            return UNKNOWN, 0.0

    # 2. Check against the canonical mapping table.
    best_match = None
    best_confidence = 0.0

    for pattern, section_type in _SECTION_MAPPINGS:
        m = pattern.search(cleaned)
        if m:
            # Confidence = how much of the heading the match covers
            match_span = m.end() - m.start()
            coverage = match_span / max(len(cleaned), 1)
            # Exact match (heading IS the pattern text, or very close) → high
            if coverage > 0.8:
                confidence = 0.95
            elif coverage > 0.5:
                confidence = 0.75
            else:
                confidence = 0.5

            if confidence > best_confidence:
                best_confidence = confidence
                best_match = section_type

    if best_match is not None and best_confidence >= 0.5:
        return best_match, best_confidence

    # 3. No confident match — return unknown.
    return UNKNOWN, 0.0