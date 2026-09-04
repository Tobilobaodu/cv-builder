"""Evidence-based matching engine — Phase 3.

Compares a structured CV profile (cv_profile_versions.structured_payload +
child tables) against a structured job post (job_post_profiles) and produces:
  - match_evidence_items with support levels
  - a match_run summary with counts

Per the non-fabrication rule: missing evidence is flagged as unsupported,
never guessed. Contradictory evidence is flagged, not silently resolved.
Surface keyword overlap is distinguished from substantive support.

Support levels (per 03-data-model.md §3):
  - supported: direct evidence exists, internally consistent
  - partially_supported: related evidence but wording/scope differs
  - unsupported: no reliable evidence found
  - contradictory: two or more sources disagree (e.g. conflicting dates
    or titles for what appears to be the same role)
  - unclear: extraction confidence for the relevant CV section is too
    low to trust either way
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from app.extraction.skills_index import canonicalize

# ──────────────────────────────────────────────────────────────────────
# Support level definitions (per 03-data-model.md)
# ──────────────────────────────────────────────────────────────────────

SUPPORTED = "supported"
PARTIALLY_SUPPORTED = "partially_supported"
UNSUPPORTED = "unsupported"
CONTRADICTORY = "contradictory"
UNCLEAR = "unclear"


@dataclass
class EvidenceItem:
    """A single match evidence item for one job requirement."""
    requirement_text: str
    requirement_type: str  # "required" | "preferred"
    support_level: str
    confidence: float
    source_references: list[str] = field(default_factory=list)
    suggestion: str | None = None
    warning: str | None = None


@dataclass
class MatchResult:
    """Complete match analysis output."""
    score: float
    supported_count: int
    partial_count: int
    unsupported_count: int
    contradictory_count: int = 0
    unclear_count: int = 0
    total_requirements: int = 0
    summary_analysis: str = ""
    evidence_items: list[EvidenceItem] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────


def run_match(
    cv_profile_payload: dict,
    cv_skills: list[str],
    job_post_profile: dict,
) -> MatchResult:
    """Compare a CV profile against a job post and return match evidence.

    Args:
        cv_profile_payload: The structured_payload from cv_profile_versions.
        cv_skills: List of skill names from cv_skill_items for this profile.
        job_post_profile: The job_post_profiles row (as a dict).

    Returns:
        MatchResult with scored evidence items.
    """
    evidence_items: list[EvidenceItem] = []

    # ── CV consistency pre-pass (contradictory detection) ──────────
    # Examine cv_experience_items for internal contradictions that would
    # make any skill claim suspect.  This creates a lookup map that
    # _match_requirement consults when a requirement touches a conflicted
    # area.
    consistency = _build_consistency_map(cv_profile_payload)

    # ── Requirements matching ────────────────────────────────────────
    # Four possible sources, not just required/preferred skills — a job
    # post's requirement content can land in any of them depending on how
    # it was written, and restricting matching to just the first two means
    # any post whose skills only show up as qualification bullets or
    # keywords never produces a match at all. Each source carries its own
    # weight (display type follows the weight: required-weight sources
    # display as "required", preferred-weight as "preferred") and is
    # deduped case-insensitively, keeping the higher-weight bucket on
    # collision. Qualifications are capped so a long bullet list doesn't
    # dominate the score; responsibilities are deliberately excluded —
    # they're job duties, not CV-checkable claims.
    required_skills = job_post_profile.get("required_skills") or []
    qualifications = (job_post_profile.get("qualifications") or [])[:15]
    preferred_skills = job_post_profile.get("preferred_skills") or []
    keywords = job_post_profile.get("keywords") or []

    all_requirements: list[tuple[str, str, float]] = []
    seen_lower: set[str] = set()
    for source, weight in (
        (required_skills, 1.0),
        (qualifications, 1.0),
        (preferred_skills, 0.5),
        (keywords, 0.5),
    ):
        display_type = "required" if weight == 1.0 else "preferred"
        for skill in source:
            key = skill.strip().lower()
            if not key or key in seen_lower:
                continue
            seen_lower.add(key)
            all_requirements.append((skill, display_type, weight))

    # Match each requirement against the CV
    cv_skills_lower = [s.lower() for s in cv_skills]
    cv_text_blob = _flatten_cv_text(cv_profile_payload).lower()

    # Certifications are derived from the payload itself (not a separate
    # query, unlike cv_skills) — both are written in the same
    # process_cv_parse step, so they're always in sync, and this avoids
    # touching process_match()'s call site for a new DB query.
    cv_certifications = [
        c.get("name") for c in (cv_profile_payload.get("certifications") or [])
        if c.get("name")
    ]
    cv_certifications_lower = [c.lower() for c in cv_certifications]

    for req_text, req_type, _weight in all_requirements:
        evidence = _match_requirement(
            req_text, req_type, cv_skills, cv_skills_lower,
            cv_text_blob, consistency, cv_profile_payload,
            cv_certifications, cv_certifications_lower,
        )
        evidence_items.append(evidence)

    # ── Compute counts ─────────────────────────────────────────────
    supported = sum(1 for e in evidence_items if e.support_level == SUPPORTED)
    partial = sum(1 for e in evidence_items if e.support_level == PARTIALLY_SUPPORTED)
    unsupported = sum(1 for e in evidence_items if e.support_level == UNSUPPORTED)
    contradictory = sum(1 for e in evidence_items if e.support_level == CONTRADICTORY)
    unclear = sum(1 for e in evidence_items if e.support_level == UNCLEAR)
    total = len(evidence_items)

    # Score: weighted by requirement type.
    # contradictory and unclear reduce the score like unsupported.
    if total == 0:
        score = 0.0
    else:
        weight = 0.0
        for e in evidence_items:
            w = 1.0 if e.requirement_type == "required" else 0.5
            if e.support_level == SUPPORTED:
                weight += w
            elif e.support_level == PARTIALLY_SUPPORTED:
                weight += w * 0.5
            # unsupported, contradictory, unclear contribute 0
        max_weight = sum(1.0 if t == "required" else 0.5 for _, t, _w in all_requirements)
        score = round(weight / max(max_weight, 1), 2)

    parts = [f"Matched {supported} of {total} requirements fully"]
    if partial:
        parts.append(f"{partial} partially supported")
    if unsupported:
        parts.append(f"{unsupported} unsupported")
    if contradictory:
        parts.append(f"{contradictory} contradictory")
    if unclear:
        parts.append(f"{unclear} unclear")
    parts.append(f"Overall score: {score * 100:.0f}%")
    summary = ", ".join(parts) + "."

    return MatchResult(
        score=score,
        supported_count=supported,
        partial_count=partial,
        unsupported_count=unsupported,
        contradictory_count=contradictory,
        unclear_count=unclear,
        total_requirements=total,
        summary_analysis=summary,
        evidence_items=evidence_items,
    )


# ──────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────


def _parse_cv_date(value) -> "date | None":
    """Parse a CV date string into a comparable date, or None if it can't be.

    CV extraction is heuristic (per the module's no-LLM design), so dates
    show up as full ISO ("2020-01-15"), year-month ("2020-01"), a bare
    year ("2020"), or occasionally free text with a year buried in it.
    Missing month/day default to January 1st — coarse, but sufficient for
    an overlap check, and this function never needs to be more precise
    than that.
    """
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None

    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    match = re.search(r"(19|20)\d{2}", value)
    if match:
        try:
            return datetime.strptime(match.group(0), "%Y").date()
        except ValueError:
            return None
    return None


def _date_ranges_overlap(entry_a: dict, entry_b: dict) -> bool | None:
    """Return whether two workExperience entries' date ranges overlap.

    Returns True/False when both entries have enough date information to
    tell, or None when they don't (e.g. no startDate on one side, or an
    open-ended range where `current` isn't set). Callers must treat None
    as "don't flag" — per _build_consistency_map's conservative-by-design
    rule, an indeterminate overlap is not evidence of a contradiction.

    Uses a strict `<` comparison at the boundary rather than `<=`: CV
    dates are frequently year- or month-granular, so two roles where one
    ends the same period the next begins (a normal back-to-back job
    change) must not register as "overlapping."
    """
    start_a = _parse_cv_date(entry_a.get("startDate") or entry_a.get("start_date"))
    start_b = _parse_cv_date(entry_b.get("startDate") or entry_b.get("start_date"))
    if start_a is None or start_b is None:
        return None

    end_a = _parse_cv_date(entry_a.get("endDate") or entry_a.get("end_date"))
    if end_a is None:
        if not entry_a.get("current"):
            return None
        end_a = date.max

    end_b = _parse_cv_date(entry_b.get("endDate") or entry_b.get("end_date"))
    if end_b is None:
        if not entry_b.get("current"):
            return None
        end_b = date.max

    return start_a < end_b and start_b < end_a


def _build_consistency_map(cv_payload: dict) -> dict[str, dict]:
    """Scan cv_experience_items for internal contradictions.

    Detects:
      - Overlapping date ranges with conflicting titles (same company,
        overlapping periods, different role names → suspicious).
      - Same title at different companies with implausibly overlapping
        date ranges.

    Returns a dict suitable for _match_requirement to consult:
        { "skill_name_lower": { "contradiction": True, "detail": "..." } }
    or empty dict if no contradictions found.

    This is intentionally conservative — it only flags clear conflicts,
    not every possible ambiguity.  False positives are worse than missed
    contradictions because they would incorrectly mark supported skills
    as contradictory.  In particular, two different role families at the
    same company are only flagged when their date ranges are confirmed to
    overlap — a normal sequential job change (different title, same
    company, non-overlapping dates) is not a contradiction, and when
    dates are missing or unparseable on either side, the overlap can't be
    confirmed, so nothing is flagged.
    """
    consistency: dict[str, dict] = {}
    exp_items = cv_payload.get("workExperience", []) or []
    if len(exp_items) < 2:
        return consistency

    for i in range(len(exp_items)):
        a = exp_items[i]
        company_a = (a.get("company") or "").lower().strip()
        title_a = (a.get("title") or "").lower().strip()
        if not company_a or not title_a:
            continue

        for j in range(i + 1, len(exp_items)):
            b = exp_items[j]
            company_b = (b.get("company") or "").lower().strip()
            title_b = (b.get("title") or "").lower().strip()
            if not company_b or not title_b:
                continue

            # Same company, different titles: check for date overlaps.
            # If the dates overlap and the titles are substantially different
            # (not "junior" vs "senior" at the same employer — that's normal
            # progression), flag as contradictory. A title change with no
            # confirmed date overlap is a normal job change, not a conflict.
            if company_a == company_b and title_a != title_b:
                if _roles_conflict(title_a, title_b) and _date_ranges_overlap(a, b):
                    detail = (
                        f"You list both '{a.get('title')}' and '{b.get('title')}' "
                        f"at {a.get('company')}, with overlapping dates. These "
                        f"titles conflict — resolve this before using either as "
                        f"evidence."
                    )
                    # Mark both entities' technologies/skills as suspect
                    for tech in (a.get("technologies") or []):
                        consistency[tech.lower().strip()] = {
                            "contradiction": True, "detail": detail,
                        }
                    for tech in (b.get("technologies") or []):
                        consistency[tech.lower().strip()] = {
                            "contradiction": True, "detail": detail,
                        }

    return consistency


def _roles_conflict(title_a: str, title_b: str) -> bool:
    """Return True if two titles at the same company are plausibly
    contradictory rather than a normal promotion.

    "Software Engineer" → "Senior Software Engineer" is NOT a conflict
    (natural progression).
    "Software Engineer" → "DevOps Engineer" at the same company IS a
    conflict (different role families at the same employer).
    """
    # Normalize: strip seniority prefixes to compare role families
    seniority_words = {"senior", "junior", "lead", "principal", "staff",
                       "mid", "associate", "head", "director", "vp",
                       "vice", "president", "chief", "cto"}
    norm_a = " ".join(w for w in title_a.split() if w not in seniority_words)
    norm_b = " ".join(w for w in title_b.split() if w not in seniority_words)
    # If the non-seniority portions are the same → promotion, not conflict
    if norm_a == norm_b:
        return False
    # Both contain "engineer" but different specializations → conflict
    if "engineer" in title_a and "engineer" in title_b:
        return norm_a != norm_b
    # Completely different titles → conflict
    return norm_a != norm_b


def _match_requirement(
    req_text: str,
    req_type: str,
    cv_skills: list[str],
    cv_skills_lower: list[str],
    cv_text_blob: str,
    consistency: dict[str, dict],
    cv_profile_payload: dict,
    cv_certifications: list[str] | None = None,
    cv_certifications_lower: list[str] | None = None,
) -> EvidenceItem:
    """Match a single requirement against CV evidence.

    Strategy (no LLM — heuristic for Phase 3 first pass):
      1. Exact skill name match in cv_skills → supported (0.85)
      1.5. Exact certification-name match → supported (0.85)
      2. Skill appears as substring in CV text → partially_supported (0.6)
      3. If the requirement touches a contradictory area of the CV →
         contradictory (0.0) with warning referencing both sources
      4. If the CV section containing the skill has low extraction
         confidence → unclear (0.3)
      5. No match anywhere → unsupported (0.0)
    """
    cv_certifications = cv_certifications or []
    cv_certifications_lower = cv_certifications_lower or []
    req_lower = req_text.strip().lower()

    # ── Check consistency map (contradictory) ──────────────────────
    if req_lower in consistency:
        info = consistency[req_lower]
        if info.get("contradiction"):
            return EvidenceItem(
                requirement_text=req_text,
                requirement_type=req_type,
                support_level=CONTRADICTORY,
                confidence=0.0,
                warning=info["detail"],
            )

    # ── 1. Exact skill name match ──────────────────────────────────
    if req_lower in cv_skills_lower:
        idx = cv_skills_lower.index(req_lower)
        skill_name = cv_skills[idx]
        return EvidenceItem(
            requirement_text=req_text,
            requirement_type=req_type,
            support_level=SUPPORTED,
            confidence=0.85,
            source_references=[f"skill:{skill_name}"],
        )

    # ── 1b. Exact certification-name match ──────────────────────────
    # Mirrors the skill exact-match tier exactly: exact list-membership
    # only, not substring. A near/fuzzy certification match still falls
    # through to step 2's fuzzy-blob check (now that _flatten_cv_text
    # includes certification text) and lands at partially_supported —
    # exact gets supported, near stays partially_supported.
    if req_lower in cv_certifications_lower:
        idx = cv_certifications_lower.index(req_lower)
        cert_name = cv_certifications[idx]
        return EvidenceItem(
            requirement_text=req_text,
            requirement_type=req_type,
            support_level=SUPPORTED,
            confidence=0.85,
            source_references=[f"certification:{cert_name}"],
        )

    # ── 1.5 ESCO synonym match ──────────────────────────────────────
    # Same concept, different wording ("UX" vs "User Experience Design")
    # — resolved via the ESCO taxonomy's canonical URI, not just literal
    # string comparison. Only fires when req_text itself is a short,
    # skill-phrase-like string (canonicalize() requires a whole-phrase
    # match) — long qualification sentences fall through to step 2
    # unchanged, since ESCO's controlled vocabulary rarely matches
    # free-form prose verbatim (confirmed directly against real job-post
    # text before wiring this in). Deliberately partially_supported, not
    # supported — a synonym match is real but indirect evidence, and this
    # codebase treats false positives as worse than missed matches.
    req_esco = canonicalize(req_text)
    if req_esco is not None:
        for skill_name, skill_lower in zip(cv_skills, cv_skills_lower):
            skill_esco = canonicalize(skill_name)
            if skill_esco is not None and skill_esco.uri == req_esco.uri:
                return EvidenceItem(
                    requirement_text=req_text,
                    requirement_type=req_type,
                    support_level=PARTIALLY_SUPPORTED,
                    confidence=0.7,
                    source_references=[f"skill:{skill_name}"],
                    suggestion=(
                        f"Your CV lists '{skill_name}', which ESCO recognizes as "
                        f"the same skill as '{req_text}' — consider using the "
                        f"job posting's exact terminology for a stronger match."
                    ),
                )

    # ── 2. Fuzzy: skill appears as substring in CV text ────────────
    if req_lower in cv_text_blob:
        # Check confidence of the CV section where this appears.
        # If extraction confidence is low, mark as unclear rather than
        # partially_supported.
        section_confidence = _get_section_confidence(
            cv_profile_payload, req_lower,
        )
        if section_confidence is not None and section_confidence < 0.5:
            return EvidenceItem(
                requirement_text=req_text,
                requirement_type=req_type,
                support_level=UNCLEAR,
                confidence=0.3,
                suggestion=(
                    f"Your CV may mention '{req_text}' but the extraction "
                    f"confidence for the relevant section is low "
                    f"({section_confidence:.0%}). Consider verifying this "
                    f"section in your uploaded document and reprocessing."
                ),
                warning=(
                    "Low extraction confidence — evidence exists but "
                    "cannot be trusted without review."
                ),
            )

        return EvidenceItem(
            requirement_text=req_text,
            requirement_type=req_type,
            support_level=PARTIALLY_SUPPORTED,
            confidence=0.6,
            suggestion=(
                f"Your CV mentions '{req_text}' but it is not explicitly listed "
                f"in your skills section. Consider adding it explicitly."
            ),
        )

    # ── 3. No match — unsupported ──────────────────────────────────
    detail = (
        "This is a required skill — address it in your cover letter "
        "or consider upskilling."
        if req_type == "required"
        else "This is a preferred skill."
    )
    return EvidenceItem(
        requirement_text=req_text,
        requirement_type=req_type,
        support_level=UNSUPPORTED,
        confidence=0.0,
        warning=f"No evidence of '{req_text}' found in your CV. {detail}",
    )


def _get_section_confidence(
    cv_payload: dict, skill_lower: str,
) -> float | None:
    """Return the confidence of the CV section containing *skill_lower*.

    This is used to determine whether a substring match in the CV text
    should be treated as `partially_supported` (high section confidence)
    or `unclear` (low section confidence — the extraction might have
    misread the content).

    Returns None if no relevant section confidence is found, in which
    case the caller treats the match as partially_supported by default.
    """
    # Check experience items — if any bullet contains the skill and
    # that experience item has low confidence, the evidence is unclear.
    for exp in cv_payload.get("workExperience", []) or []:
        bullets = " ".join(exp.get("bullets", []) or []).lower()
        technologies = " ".join(exp.get("technologies", []) or []).lower()
        if skill_lower in bullets or skill_lower in technologies:
            conf = exp.get("confidence")
            if conf is not None and isinstance(conf, (int, float)):
                return float(conf)

    # Check skills section confidence summary
    confidence_summary = cv_payload.get("confidenceSummary") or cv_payload.get("confidence_summary") or {}
    skills_conf = confidence_summary.get("skills")
    if skills_conf is not None and isinstance(skills_conf, (int, float)):
        return float(skills_conf)

    return None


def _flatten_cv_text(payload: dict) -> str:
    """Extract all plain text from a CV structured payload for substring matching."""
    parts = []

    basics = payload.get("basics", {}) or {}
    if basics.get("summary"):
        parts.append(str(basics["summary"]))

    for exp in payload.get("workExperience", []) or []:
        parts.append(str(exp.get("company", "")))
        parts.append(str(exp.get("title", "")))
        for bullet in exp.get("bullets", []) or []:
            parts.append(str(bullet))
        for tech in exp.get("technologies", []) or []:
            parts.append(str(tech))

    skills = payload.get("skills", {}) or {}
    for cat in ("technical", "soft"):
        for s in skills.get(cat, []) or []:
            parts.append(str(s))

    # Education/certifications: checkable qualifications evidence — a job's
    # degree/certification requirement was structurally unable to match
    # anything before this, since this function never read these keys.
    for edu in payload.get("education", []) or []:
        parts.append(str(edu.get("institution") or ""))
        parts.append(str(edu.get("degree") or ""))
        parts.append(str(edu.get("field") or ""))

    for cert in payload.get("certifications", []) or []:
        parts.append(str(cert.get("name") or ""))
        parts.append(str(cert.get("issuer") or ""))

    # Projects: general technical evidence, not a qualifications claim —
    # kept out of the "education" categorization at the evidence_binder/
    # tailored_cv_generation layer, but safe to flow into this shared
    # matching blob since this function does undifferentiated substring
    # matching and never needs to know which CV section a hit came from.
    for proj in payload.get("projects", []) or []:
        parts.append(str(proj.get("name") or ""))
        parts.append(str(proj.get("description") or ""))
        for bullet in proj.get("bullets", []) or []:
            parts.append(str(bullet))
        for tech in proj.get("technologies", []) or []:
            parts.append(str(tech))

    return " ".join(parts)