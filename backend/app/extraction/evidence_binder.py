"""Evidence binding and verification for tailored CV generation.

Two jobs, both deterministic — no DB access, no LLM call, mirrors
match_engine.py's pure-function style:

1. bind_evidence_pool(): re-derives which real CvExperienceItem/
   CvEducationItem/CvSkillItem/CvCertificationItem/CvProjectItem rows
   actually back a supported/partially_supported MatchEvidenceItem.
   Deliberately does NOT trust
   MatchEvidenceItem.source_references — that field is populated for
   exactly one of five support-level branches in match_engine.py (the
   exact-skill-match case), and even then only with a "skill:<name>"
   tag string, not a real row id. Everything else is empty. Re-deriving
   fresh, from the CV's *current* rows, also avoids citing stale content
   if the CV was edited between match time and (re)generation.

2. verify_claim_against_evidence(): the independent, post-generation
   check that a *generated* claim's content is actually supported by the
   real text of the rows cited for it — not merely that a syntactically
   valid id was cited. This is the literal implementation of
   10-security-plan.md's evidence-reference content-verification
   requirement (05 gap #3 / §5): "does the referenced id's content
   actually contain text supporting this specific claim."
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── Evidence pool ────────────────────────────────────────────────────

EXPERIENCE = "experience"
EDUCATION = "education"
SKILL = "skill"
CERTIFICATION = "certification"
PROJECT = "project"
ANSWER = "answer"

# Support levels permitted to feed generation at all, per 03-data-model.md's
# support-level table. unsupported/contradictory/unclear never reach the
# pool — they only ever surface via the improvement checklist.
_GENERATION_ELIGIBLE_SUPPORT_LEVELS = frozenset({"supported", "partially_supported"})


@dataclass
class EvidenceCandidate:
    row_type: str          # "experience" | "education" | "skill" | "certification" | "project" | "answer"
    row_id: str
    searchable_text: str    # the row's own real content, lowercased comparison happens at match time


def _experience_candidate(item) -> EvidenceCandidate:
    parts = [item.title or "", item.company or ""]
    parts.extend(item.bullets or [])
    parts.extend(item.technologies or [])
    return EvidenceCandidate(EXPERIENCE, item.id, " ".join(p for p in parts if p))


def _education_candidate(item) -> EvidenceCandidate:
    parts = [item.institution or "", item.degree or "", item.field or ""]
    return EvidenceCandidate(EDUCATION, item.id, " ".join(p for p in parts if p))


def _skill_candidate(item) -> EvidenceCandidate:
    return EvidenceCandidate(SKILL, item.id, item.skill_name or "")


def _certification_candidate(item) -> EvidenceCandidate:
    parts = [item.name or "", item.issuer or ""]
    return EvidenceCandidate(CERTIFICATION, item.id, " ".join(p for p in parts if p))


def _project_candidate(item) -> EvidenceCandidate:
    parts = [item.name or "", item.description or ""]
    parts.extend(item.bullets or [])
    parts.extend(item.technologies or [])
    return EvidenceCandidate(PROJECT, item.id, " ".join(p for p in parts if p))


def _answer_candidate(question_text: str, answer_item) -> EvidenceCandidate:
    """Pairs the answer with its question text for grounding — a bare
    answer like 'because I love their mission' has almost no token
    overlap with anything on its own for verify_claim_against_evidence's
    check; the question text gives real context for free."""
    text = f"{question_text} — {answer_item.answer_text}" if question_text else (answer_item.answer_text or "")
    return EvidenceCandidate(ANSWER, answer_item.id, text)


def build_answer_candidates(questions_by_id: dict, answers: list) -> list[EvidenceCandidate]:
    """Cover-letter-specific: a candidate's own Q&A answers, unconditionally
    includable as real, citable evidence (unlike CV rows, these are never
    relevance-filtered by the caller — each was purpose-written by the
    user for this exact application)."""
    return [
        _answer_candidate(
            getattr(questions_by_id.get(a.question_id), "question_text", ""), a,
        )
        for a in answers
    ]


def build_candidate_pool(
    experience_items, education_items, skill_items,
    certification_items=None, project_items=None,
) -> list[EvidenceCandidate]:
    """Build the full candidate list from a CV profile version's child rows."""
    candidates = [_experience_candidate(e) for e in experience_items]
    candidates.extend(_education_candidate(e) for e in education_items)
    candidates.extend(_skill_candidate(s) for s in skill_items)
    candidates.extend(_certification_candidate(c) for c in (certification_items or []))
    candidates.extend(_project_candidate(p) for p in (project_items or []))
    return candidates


def _find_matches(item, all_candidates: list[EvidenceCandidate]) -> list[EvidenceCandidate]:
    """All real rows whose own content overlaps with one evidence item's
    requirement text — re-deriving what match_engine.py's substring/exact-
    match check found, but tracking *which row* produced the hit (which
    match_engine.py never needed to, and doesn't do)."""
    req_text = (item.requirement_text or "").strip().lower()
    if not req_text:
        return []

    matches = []
    for candidate in all_candidates:
        cand_text = candidate.searchable_text.strip().lower()
        if not cand_text:
            continue

        if candidate.row_type in (SKILL, CERTIFICATION):
            # Skills and certifications match on equality or containment
            # either direction — mirrors match_engine's exact-match branch
            # plus enough slack for "Docker" vs "Docker/Kubernetes"-style
            # values. A credential's name matters as a whole, not as a
            # fuzzy substring, so certifications get the same treatment
            # as skills here, not the education/project fuzzy path.
            is_match = (
                req_text == cand_text
                or req_text in cand_text
                or cand_text in req_text
            )
        else:
            # Experience/education/project match on substring containment,
            # mirroring match_engine's fuzzy-substring branch.
            is_match = req_text in cand_text

        if is_match:
            matches.append(candidate)

    return matches


def bind_evidence_pool(evidence_items, all_candidates: list[EvidenceCandidate]) -> list[EvidenceCandidate]:
    """For supported/partially_supported evidence items only, the
    deduplicated union of every real row that backs at least one of them.

    A requirement with no matching row (can legitimately happen — the
    original match's flattened text blob spans multiple rows, so a
    single-row re-derivation is strictly more conservative) contributes
    nothing to the pool. That's correct, not a bug: no candidate means no
    usable evidence for that requirement, and generation must omit it.
    """
    pool: list[EvidenceCandidate] = []
    seen_ids: set[str] = set()

    for item in evidence_items:
        if item.support_level not in _GENERATION_ELIGIBLE_SUPPORT_LEVELS:
            continue
        for candidate in _find_matches(item, all_candidates):
            if candidate.row_id not in seen_ids:
                pool.append(candidate)
                seen_ids.add(candidate.row_id)

    return pool


def _count_relevance(
    evidence_items, all_candidates: list[EvidenceCandidate], row_type: str,
) -> dict[str, int]:
    """For each candidate of one row_type, how many generation-eligible
    evidence items' requirements matched it. Reuses the exact same
    matching logic as bind_evidence_pool, not a separate heuristic."""
    counts: dict[str, int] = {}
    for item in evidence_items:
        if item.support_level not in _GENERATION_ELIGIBLE_SUPPORT_LEVELS:
            continue
        for candidate in _find_matches(item, all_candidates):
            if candidate.row_type == row_type:
                counts[candidate.row_id] = counts.get(candidate.row_id, 0) + 1
    return counts


def count_experience_relevance(evidence_items, all_candidates: list[EvidenceCandidate]) -> dict[str, int]:
    """Used by the generation orchestrator to rank which experience items
    get their own generation call when there are more eligible items than
    settings.tailored_cv_max_experience_items allows."""
    return _count_relevance(evidence_items, all_candidates, EXPERIENCE)


def count_project_relevance(evidence_items, all_candidates: list[EvidenceCandidate]) -> dict[str, int]:
    """Used by the generation orchestrator to rank project display order
    when there are more projects than settings.tailored_cv_max_project_items
    allows — ranks order only, never gates which projects are attempted
    (unlike experience, a project with zero relevance is still generated)."""
    return _count_relevance(evidence_items, all_candidates, PROJECT)


# ── Verification ─────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# No trailing \b: a digit run immediately followed by a magnitude letter
# (e.g. "50M", "10K") has no word-boundary between the digit and the
# letter, so a \b-anchored pattern silently fails to match the whole
# token — verified directly: \b\d[\d,]*\.?\d*%?\b matches nothing at all
# on "50M requests". Anchoring only on \d[\d,]*\.?\d*, then optionally
# consuming a trailing %/K/M/B, avoids that false-negative entirely.
_NUMBER_RE = re.compile(r"\$?\d[\d,]*\.?\d*%?[KkMmBb]?")
_PROPER_NOUN_RE = re.compile(r"\b(?:[A-Z][a-zA-Z]*\s+){1,4}[A-Z][a-zA-Z]*\b")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


_SENTENCE_START_RE = re.compile(r"[.!?]\s+$")


def _extract_facts(text: str) -> list[str]:
    """Digit sequences (numbers, percentages, years, dollar amounts) and
    multi-word capitalized spans (named entities: employers, products,
    certifications) — the two categories of content most likely to be a
    specific, checkable, fabrication-risk claim rather than a rephrasing.
    Single capitalized words are deliberately excluded (too noisy — every
    sentence-initial word is capitalized); requiring 2+ consecutive
    capitalized words trades recall for precision, consistent with this
    check's role as a hard reject gate, not a style linter.

    Caught during testing: a claim like "Experienced Python engineer..."
    matched _PROPER_NOUN_RE as a single 2-word span ("Experienced
    Python") because ordinary sentence-initial capitalization ("Experienced")
    sits directly next to a real proper noun ("Python") — the resulting
    "fact" then fails verification even though "Python" alone is
    genuinely grounded, a false positive that would reject good content
    for no real safety benefit. When a match starts at the beginning of
    the text or right after a sentence boundary, its first word is
    dropped and only the remainder (if 2+ words are still left) is kept
    as a fact — "Google Cloud Platform" at a sentence start still yields
    "Cloud Platform" to check, but "Experienced Python" correctly yields
    nothing (one word left, below the multi-word threshold).

    Caught during cover letter generation testing: "At Acme Corp I built..."
    (no comma before "I") matches as a single 3-word span ("Acme Corp
    I") because the capitalized first-person pronoun sits directly next
    to a real proper noun with only whitespace between them — the
    trailing "I" is never part of the entity and is always capitalized
    regardless of position, so it's stripped from the end of any span
    unconditionally (not just at sentence boundaries, unlike the
    sentence-start case above, since "I" can appear anywhere).
    """
    facts = list(_NUMBER_RE.findall(text))
    for match in _PROPER_NOUN_RE.finditer(text):
        span_text = match.group(0)
        preceding = text[:match.start()]
        at_sentence_start = match.start() == 0 or bool(_SENTENCE_START_RE.search(preceding))
        if at_sentence_start:
            remaining_words = span_text.split()[1:]
            if len(remaining_words) < 2:
                continue
            span_text = " ".join(remaining_words)

        words = span_text.split()
        if words and words[-1] == "I":
            words = words[:-1]
            if len(words) < 2:
                continue
            span_text = " ".join(words)

        facts.append(span_text)
    return facts


@dataclass
class VerificationResult:
    passed: bool
    reason: str | None = None
    unsupported_facts: list[str] | None = None


def verify_claim_against_evidence(
    claim_text: str,
    evidence_texts: list[str],
    overlap_threshold: float,
) -> VerificationResult:
    """Claim/entity verification against the *real* content of the cited
    evidence rows — not whether a reference id was merely present and
    well-formed.

    1. Hard fact check (primary): every number/named-entity-like span in
       the claim must appear in the cited evidence. This blocks
       fabrication — a claim that's mostly grounded but has one invented
       number or name slipped in — while permitting genuine rewriting: a
       stronger-but-truthful rephrase with lower token overlap passes
       because it is judged on its facts, not its phrasing.
    2. Token-overlap floor (fallback only): when the claim extracts to no
       checkable facts at all, some token grounding is required so
       wholesale off-topic invention is still caught.
    """
    if not claim_text or not claim_text.strip():
        return VerificationResult(passed=False, reason="empty claim text")

    combined_evidence = " ".join(evidence_texts)
    if not combined_evidence.strip():
        return VerificationResult(passed=False, reason="no evidence text to verify against")

    combined_lower = combined_evidence.lower()
    facts = _extract_facts(claim_text)
    unsupported = [fact for fact in facts if fact.lower() not in combined_lower]
    if unsupported:
        return VerificationResult(
            passed=False,
            reason=f"unsupported facts not present in cited evidence: {unsupported}",
            unsupported_facts=unsupported,
        )

    # Primary gate (facts) passed. When the claim extracted no checkable
    # facts at all, fall back to a token-overlap floor so wholesale
    # off-topic invention (no numbers, no named entities to check) is
    # still caught — genuine rewriting is otherwise not penalized for
    # low overlap.
    if not facts:
        claim_tokens = _tokenize(claim_text)
        if not claim_tokens:
            return VerificationResult(passed=False, reason="claim has no comparable tokens")
        evidence_tokens = _tokenize(combined_evidence)
        overlap_ratio = len(claim_tokens & evidence_tokens) / len(claim_tokens)
        if overlap_ratio < overlap_threshold:
            return VerificationResult(
                passed=False,
                reason=f"token overlap {overlap_ratio:.2f} below threshold {overlap_threshold:.2f}",
            )

    return VerificationResult(passed=True)
