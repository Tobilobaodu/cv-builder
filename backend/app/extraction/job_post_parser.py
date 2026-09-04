"""Job post structuring parsers.

Defines the swappable JobPostParser ABC (same pattern as DocumentParser)
and a rules-based default implementation. An LLM-backed parser can be
dropped in later without changing callers.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pydantic import BaseModel

from app.extraction.skills_index import match_terms as _esco_match_terms


class JobPostProfileResult(BaseModel):
    """Structured job post output matching job_post_profiles shape."""
    job_title: str | None = None
    employer: str | None = None
    location: str | None = None
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    responsibilities: list[str] | None = None
    qualifications: list[str] | None = None
    keywords: list[str] | None = None
    seniority: str | None = None  # nullable — never guessed
    confidence: float | None = None


class JobPostParser(ABC):
    """Abstract base for job post structuring parsers.

    Implementations take raw job post text and return a structured
    JobPostProfileResult. Callers never depend on a specific parser.
    """

    @abstractmethod
    def parse(self, raw_text: str) -> JobPostProfileResult:
        """Parse raw job post text into structured fields.

        Args:
            raw_text: The raw text of the job post (from URL fetch or pasted).

        Returns:
            JobPostProfileResult with extracted fields.
        """
        ...


# ──────────────────────────────────────────────────────────────────────
# Rules-based default implementation
# ──────────────────────────────────────────────────────────────────────

# Section markers — case-insensitive patterns that signal the start of
# a new section in a job post.
_SECTION_PATTERNS = [
    (re.compile(r"(?:about|overview|summary|the\s+role)", re.I),
     "description"),
    (re.compile(r"(?:responsibilit|duties?|what\s+you.{1,3}ll\s+do|key\s+acc)", re.I),
     "responsibilities"),
    (re.compile(r"(?:requirement|qualification|what\s+you\s+bring|must\s+have|who\s+you\s+are)", re.I),
     "requirements"),
    (re.compile(r"(?:preferred|nice\s+to\s+have|bonus|desired|ideally)", re.I),
     "preferred"),
    (re.compile(r"(?:benefit|perk|compensation|salary|what\s+we\s+offer)", re.I),
     "benefits"),
    (re.compile(r"(?:about\s+(?:us|the\s+company|the\s+team)|who\s+we\s+are)", re.I),
     "about_company"),
]

# Common tech/skill terms worth extracting as keywords
_COMMON_TECH_KEYWORDS = re.compile(
    r"\b(?:Python|JavaScript|TypeScript|Java|Go|Rust|C\+\+|"
    r"React|Angular|Vue|Node\.js|Django|Flask|FastAPI|Spring|"
    r"Docker|Kubernetes|AWS|Azure|GCP|Terraform|Kafka|Redis|PostgreSQL|MongoDB|"
    r"Git|CI/CD|Agile|Scrum|ML|AI|Machine\s+Learning|SQL|NoSQL|REST|GraphQL|"
    r"HTML|CSS|Linux|Unix|Windows|MacOS)\b",
    re.I,
)


class RulesBasedJobPostParser(JobPostParser):
    """Fast, no-LLM parser using section-detection heuristics.

    Handles ~80% of job posts. The remainder can be handled by an
    LLM-backed parser dropped in behind the same interface later.
    """

    def parse(self, raw_text: str) -> JobPostProfileResult:
        text = raw_text.strip()
        if not text:
            return JobPostProfileResult(confidence=0.0)

        lines = text.split("\n")

        # Detect sections
        sections = self._segment_sections(lines)

        # Extract fields
        title = self._extract_title(lines)
        employer = self._extract_employer(text)
        location = self._extract_location(text)

        req_text = sections.get("requirements", "")
        pref_text = sections.get("preferred", "")
        resp_lines = self._extract_bullet_list(
            sections.get("responsibilities", "")
        )

        required_skills = self._extract_skills(req_text)
        preferred_skills = self._extract_skills(pref_text)
        qualifications = self._extract_bullet_list(req_text)
        keywords = self._extract_keywords(text)

        seniority = self._extract_seniority(text)

        # Confidence: higher if we found multiple sections
        found_sections = sum(1 for v in sections.values() if v)
        confidence = min(0.9, 0.3 + found_sections * 0.12)

        return JobPostProfileResult(
            job_title=title,
            employer=employer,
            location=location,
            required_skills=required_skills or None,
            preferred_skills=preferred_skills or None,
            responsibilities=resp_lines or None,
            qualifications=qualifications or None,
            keywords=keywords or None,
            seniority=seniority,
            confidence=round(confidence, 2),
        )

    # ── Internal helpers ────────────────────────────────────────────

    def _segment_sections(self, lines: list[str]) -> dict[str, str]:
        """Split the job post into named sections based on heading patterns."""
        sections: dict[str, list[str]] = {}
        current_section = "preamble"

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check if this line is a section heading
            matched = False
            for pattern, section_name in _SECTION_PATTERNS:
                if pattern.search(stripped) and len(stripped) < 80:
                    current_section = section_name
                    matched = True
                    break

            if not matched:
                sections.setdefault(current_section, []).append(stripped)

        return {k: "\n".join(v) for k, v in sections.items()}

    # Module-level bullet pattern — matches symbolic bullets and numbered lists.
    # The previous inline regex had a malformed character class (invalid range
    # inside [\d+[.)]]) that silently matched nothing, so every job post's
    # responsibilities and qualifications parsed as empty.
    _BULLET_RE = re.compile(r"^\s*(?:[-•*✦➤►]|\d+[.)])\s+")

    # Sentence-terminal punctuation — a glyph-less candidate line ending in
    # one of these reads as prose, not a list item.
    _SENTENCE_END_RE = re.compile(r"[.…?!:]\s*$")

    @staticmethod
    def _extract_bullet_list(text: str) -> list[str]:
        """Extract bullet-point items from section text.

        Symbolic/numbered bullets are the primary path. When a section has
        none at all, many real postings (copy-pasted from a webpage's <li>
        elements) render list items as plain lines with no glyph — fall
        back to treating short, non-prose-looking lines as items, but only
        when there are at least 2 of them, so an ordinary short paragraph
        doesn't get misread as a list.
        """
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        items = []
        for stripped in lines:
            if RulesBasedJobPostParser._BULLET_RE.match(stripped):
                clean = RulesBasedJobPostParser._BULLET_RE.sub("", stripped).strip()
                if clean:
                    items.append(clean)
        if items:
            return items

        candidates = [
            line for line in lines
            if len(line) <= 160 and not RulesBasedJobPostParser._SENTENCE_END_RE.search(line)
        ]
        if len(candidates) >= 2:
            return candidates
        return []

    @staticmethod
    def _extract_title(lines: list[str]) -> str | None:
        """Extract job title from the preamble — the lines before the first
        recognized section heading.

        A fixed lines[:5] window fails when the real opening lines are long
        descriptive paragraphs (common on real postings that open with
        company/product context): it skips past them into whatever short
        line comes next, which is often itself a section heading. Scanning
        only the true preamble avoids picking a heading as the title, and
        returns None — never a guess — when nothing confident is found.
        """
        preamble: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            is_heading = any(
                pattern.search(stripped) and len(stripped) < 80
                for pattern, _ in _SECTION_PATTERNS
            )
            if is_heading:
                break
            preamble.append(stripped)

        for stripped in preamble:
            if stripped.startswith("http") or len(stripped) >= 120:
                continue
            if re.match(r"^(location|remote|full.time|part.time|salary)",
                        stripped, re.I):
                continue
            return stripped
        return None

    @staticmethod
    def _extract_employer(text: str) -> str | None:
        """Try to extract employer name — conservative, returns None if unsure."""
        # Look for "at [Company]" or "Company is looking for" patterns
        m = re.search(r"\bat\s+([A-Z][\w\s&.]+?)(?:\s*[,-]|\s*$)", text)
        if m:
            name = m.group(1).strip()
            if 2 < len(name) < 60:
                return name

        m = re.search(r"^([A-Z][\w\s&.]+?)\s+(?:is|are)\s+(?:looking|hiring|seeking)",
                      text, re.M)
        if m:
            name = m.group(1).strip()
            if 2 < len(name) < 60:
                return name
        return None

    @staticmethod
    def _extract_location(text: str) -> str | None:
        """Extract location if explicitly stated."""
        m = re.search(
            r"(?:location|remote)[:\s]+([A-Za-z\s,./-]+?)(?:\n|$|\s*[-•])",
            text, re.I,
        )
        if m:
            loc = m.group(1).strip()
            if 2 < len(loc) < 100 and not loc.startswith("http"):
                return loc
        return None

    @staticmethod
    def _extract_skills(text: str) -> list[str]:
        """Extract technology/skill names from text.

        Two sources, combined: the fixed software-engineering keyword
        list (fast, precise, but scoped to one domain) and the ESCO
        taxonomy (broader domain coverage, multi-word phrases only —
        confirmed directly against real postings that single-word ESCO
        matches are too often generic/context-dependent to trust, e.g.
        "design" resolving to an unrelated narrow concept).
        """
        found = set()
        for match in _COMMON_TECH_KEYWORDS.finditer(text):
            found.add(match.group(0))
        for skill_match in _esco_match_terms(text):
            found.add(skill_match.canonical_label)
        return sorted(found)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract meaningful keywords beyond tech skills."""
        # Combine tech keywords with other domain terms
        tech = set(RulesBasedJobPostParser._extract_skills(text))

        # Look for additional patterns: years of experience, degree requirements
        extras = set()
        m = re.search(r"(\d+[\+-]?\s*(?:years?|yrs?)\s*(?:of\s+)?experience)", text, re.I)
        if m:
            extras.add(m.group(1))

        m = re.search(r"(bachelor|master|phd|ph\.d|mba)[\s']*s?\s*(?:degree|level)?",
                      text, re.I)
        if m:
            extras.add(m.group(0).strip())

        return sorted(tech | extras)

    @staticmethod
    def _extract_seniority(text: str) -> str | None:
        """Extract explicit seniority level. Returns None if not stated.

        Only matches explicit level labels — never infers from title
        conventions alone. Per the non-fabrication rule: structure is
        subject to the same discipline as content.
        """
        patterns = [
            (r"\bprincipal\b", "Principal"),
            (r"\blead\b", "Lead"),
            (r"\bsenior\b", "Senior"),
            (r"\bmid[- ]level\b", "Mid"),
            (r"\bmid\b", "Mid"),
            (r"\bjunior\b", "Junior"),
            (r"\bentry[- ]level\b", "Junior"),
        ]
        for pattern, level in patterns:
            if re.search(pattern, text, re.I):
                return level
        return None