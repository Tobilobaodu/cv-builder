"""Lookup/normalization layer over the ESCO skills taxonomy
(app/data/skills_index.json, built by scripts/ingest_esco.py).

This is a supplement to, not a replacement for, the existing hardcoded
tech-keyword list in job_post_parser.py: ESCO's labels lean toward
verb-phrase competency descriptions ("ensure infrastructure
accessibility", "use specialised design software") rather than the short
tool/brand names that dominate real CVs and job posts (confirmed
directly against the real dataset: "Figma", "React", "Docker" don't
appear as ESCO labels at all — expected, since ESCO is a government
-maintained, vendor-neutral occupational taxonomy). What it adds is
genuine new coverage for professional-competency phrasing across every
domain, not just software engineering — which is exactly the gap M1
left unfixed (M1 only widened *which* extracted fields feed matching,
not *what vocabulary* can be recognized at all).

Fully offline: the index is a static, committed JSON file, loaded once
and cached — no network access at parse time, consistent with the
no_internet network the cv_parse/ats_check workers already run on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "skills_index.json"


@dataclass(frozen=True)
class SkillMatch:
    canonical_label: str
    uri: str
    matched_text: str


def _normalize(text: str) -> str:
    """Same normalization used at ingest time — keys and query text must
    be comparable."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


@lru_cache(maxsize=1)
def _load_index() -> dict[str, dict]:
    """Lazy-loaded, process-wide singleton — the 5MB file is parsed once
    per worker process, not per call."""
    if not _INDEX_PATH.exists():
        return {}
    with open(_INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _load_phrase_lookup() -> list[tuple[str, str, str]]:
    """Flattened (normalized_phrase, canonical_label, uri) for every
    label and alt_label, sorted longest-phrase-first so a multi-word
    match ("user research") is preferred over a shorter one ("research")
    that happens to be a substring of it. Used by match_terms()'s
    text-scanning sweep, which needs this specific sort order — not a
    fast lookup structure, see _load_canonical_lookup() for that."""
    index = _load_index()
    entries: list[tuple[str, str, str]] = []
    for canonical_key, data in index.items():
        label = data["label"]
        uri = data["uri"]
        entries.append((canonical_key, label, uri))
        for alt in data.get("alt_labels", []):
            alt_key = _normalize(alt)
            if alt_key and alt_key != canonical_key:
                entries.append((alt_key, label, uri))
    entries.sort(key=lambda e: len(e[0]), reverse=True)
    return entries


@lru_cache(maxsize=1)
def _load_canonical_lookup() -> dict[str, tuple[str, str]]:
    """O(1) exact-term lookup: normalized phrase -> (canonical_label, uri),
    covering both canonical labels and alt-labels. Separate from
    _load_phrase_lookup()'s sorted list — canonicalize() is called once
    per requirement per CV skill in match_engine.py, so it needs a dict,
    not a linear scan over ~100k entries."""
    lookup: dict[str, tuple[str, str]] = {}
    for phrase_key, label, uri in _load_phrase_lookup():
        # First-seen wins (the list is longest-first, not meaningful here,
        # but a stable choice beats an arbitrary later overwrite).
        lookup.setdefault(phrase_key, (label, uri))
    return lookup


def normalize_skill(text: str) -> str:
    """Public wrapper — same normalization rule used for index keys."""
    return _normalize(text)


def canonicalize(term: str) -> SkillMatch | None:
    """Exact lookup: does this term (as a whole phrase) match an ESCO
    label or alt-label? Returns None rather than guessing at a fuzzy
    match — canonicalize is for exact/alias resolution, not discovery."""
    key = _normalize(term)
    if not key:
        return None
    hit = _load_canonical_lookup().get(key)
    if hit is None:
        return None
    label, uri = hit
    return SkillMatch(canonical_label=label, uri=uri, matched_text=term)


def match_terms(text: str, min_words: int = 2) -> list[SkillMatch]:
    """Scan a block of free text for ESCO-recognized skill phrases.

    Longest-phrase-first sweep: once a span of the text is claimed by a
    match, shorter overlapping candidates aren't also reported — matching
    "user experience design" as one skill, not that plus "experience" and
    "design" as three unrelated ones.

    min_words defaults to 2 (not a character-length floor) — checked
    directly against the real data: single-word ESCO labels/alt-labels
    are a small minority (~1,750 of ~100,000 total phrases) but a
    disproportionate source of false positives, since a single common
    word ("design", "craft") can be an alt-label for an unrelated,
    narrowly-scoped concept ("think creatively") that doesn't generalize
    when the word appears in ordinary text. Multi-word phrases are far
    more precise signals of real skill overlap.
    """
    normalized_text = _normalize(text)
    if not normalized_text:
        return []

    claimed = [False] * len(normalized_text)
    results: list[SkillMatch] = []
    seen_labels: set[str] = set()

    for phrase_key, label, uri in _load_phrase_lookup():
        if len(phrase_key.split()) < min_words:
            continue
        if label in seen_labels:
            continue
        start = 0
        while True:
            idx = normalized_text.find(phrase_key, start)
            if idx == -1:
                break
            end = idx + len(phrase_key)
            # Word-boundary check — avoid matching "go" inside "google".
            before_ok = idx == 0 or not normalized_text[idx - 1].isalnum()
            after_ok = end == len(normalized_text) or not normalized_text[end].isalnum()
            if before_ok and after_ok and not any(claimed[idx:end]):
                for i in range(idx, end):
                    claimed[i] = True
                results.append(SkillMatch(canonical_label=label, uri=uri, matched_text=phrase_key))
                seen_labels.add(label)
                break
            start = idx + 1

    return results


def literal_coverage(cv_text: str, priority_keywords: list[str]) -> dict:
    """What fraction of a job's priority keywords appear, literally, in
    the CV text — jbs-solution-sheet.md Q1.

    Deliberately dumb relative to match_terms() above: a straight
    normalized-substring test against normalize_skill(cv_text), not an
    ESCO lookup or synonym match. That's the point — this is the strict-
    parser's own bar (Taleo/Lever-class ATS keyword matching has no
    synonym handling either), reported *alongside* the LLM's semantic
    score, not instead of it. Deterministic, no model call: runs in
    single-digit milliseconds, so it costs nothing on the 30-second-
    target clock (jbs-solution-sheet.md Workstream 1).
    """
    cv_norm = normalize_skill(cv_text)
    present = [k for k in priority_keywords if normalize_skill(k) and normalize_skill(k) in cv_norm]
    present_set = set(present)
    return {
        "coverage": round(len(present) / len(priority_keywords), 2) if priority_keywords else 0.0,
        "present": present,
        "absent": [k for k in priority_keywords if k not in present_set],
    }
