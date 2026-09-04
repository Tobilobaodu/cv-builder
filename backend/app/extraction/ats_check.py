"""ATS structural-readiness checker — Product Extension #1 (11-product-extensions.md §1).

A deterministic, rules-based composite score evaluating whether a real-world
ATS can parse a CV's *structure* well enough to extract its content. Distinct
from cv_raw_text.structural_validation_result, which compares Docling against
Textract; this module evaluates the merged result against known
ATS-parsing-hostile patterns, independent of which parser produced what.

Pure functions only — no DB, no async, no LLM calls. Trivially unit-testable;
mirrors the structure of heading_canonicalizer.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Scoring constants ──────────────────────────────────────────────────

SEVERITY_DEDUCTIONS = {"high": 0.25, "medium": 0.10, "low": 0.05}
CONTACT_INFO_UNPARSEABLE_CAP = 0.40
CONTACT_INFO_TOP_DOC_FRACTION = 0.10

# ── Check type constants ───────────────────────────────────────────────

TEXT_IN_IMAGE = "text_in_image"
LAYOUT_STRUCTURE = "layout_structure"
CONTACT_INFO_LOCATION = "contact_info_location"
NON_STANDARD_CHARACTERS = "non_standard_characters"
SECTION_HEADING_RECOGNIZABILITY = "section_heading_recognizability"
FILE_FORMAT_SIGNALS = "file_format_signals"

_ALL_CHECK_TYPES = frozenset({
    TEXT_IN_IMAGE, LAYOUT_STRUCTURE, CONTACT_INFO_LOCATION,
    NON_STANDARD_CHARACTERS, SECTION_HEADING_RECOGNIZABILITY,
    FILE_FORMAT_SIGNALS,
})

# ── Non-standard character pattern ─────────────────────────────────────

# Characters and symbol classes that ATS parsers commonly drop or garble:
# private-use Unicode, font-control chars, decorative symbols-as-bullets
# outside the standard •/– set, and zero-width / bidi override markers.
_NON_STANDARD_CHAR_RE = re.compile(
    r'[\uE000-\uF8FF\uFFF0-\uFFFF'
    r'\u200B-\u200F\u2028-\u202F\u2060-\u2064\uFEFF'
    r'\U0001F000-\U0001FFFF'
    r'\u2600-\u27BF'    # misc symbols/dingbats
    r']'
)

# ── Problematic PDF producer substrings ─────────────────────────────────

_PROBLEMATIC_PDF_PRODUCERS = [
    "QuarkXPress",
    "Adobe InDesign",
    "Scribus",
    "LibreOffice 6",
    "LibreOffice 5",
    "Pages",
    "Mac OS X 1",
]


# ── Result types ───────────────────────────────────────────────────────


@dataclass
class AtsCheckResult:
    """The full ATS readiness report for a CV."""

    overall_score: float           # 0.0–1.0
    checks: list[dict]             # [{checkType, passed, severity, detail}, …]
    contact_info_parseable: bool


# ── Helpers ────────────────────────────────────────────────────────────


def _make_check_item(*, check_type: str, passed: bool, severity: str,
                     detail: str) -> dict:
    """Normalise a single check's output."""
    return {
        "check_type": check_type,
        "passed": passed,
        "severity": severity,
        "detail": detail,
    }

# ──────────────────────────────────────────────────────────────────────
# Individual check functions
# ──────────────────────────────────────────────────────────────────────


def check_text_in_image(*, docling_text: str, textract_text: str,
                        ocr_used: bool) -> dict:
    """Content present in Textract OCR but absent from Docling native text
    on a document that isn't a scanned original — a strong signal content
    is baked into an image rather than being real, selectable text.

    Severity: always **high** per the spec.
    """
    if ocr_used:
        return _make_check_item(
            check_type=TEXT_IN_IMAGE,
            passed=True,
            severity="low",
            detail="Scanned original — text-in-image is expected behaviour.",
        )

    docling_words: set[str] = set(docling_text.lower().split())
    textract_words: set[str] = set(textract_text.lower().split())
    image_only = textract_words - docling_words

    if image_only:
        sample = sorted(image_only)[:10]
        return _make_check_item(
            check_type=TEXT_IN_IMAGE,
            passed=False,
            severity="high",
            detail=f"Text found only in OCR pass, not native text — "
                   f"likely image-embedded (sample: {', '.join(sample)}).",
        )

    return _make_check_item(
        check_type=TEXT_IN_IMAGE,
        passed=True,
        severity="low",
        detail="No image-embedded text detected.",
    )


def check_layout_structure(*, structural_validation: dict | None,
                           docling_text: str = "",
                           textract_text: str = "") -> dict:
    """Multi-column / table-based layout signalling.

    Reuses the existing readingOrderConsistent flag from
    structural_validation_result.  When False, grades severity by how
    badly the two passes disagree on line count.

    Severity: medium–high per the spec, graded here from the line ratio.
    """
    if structural_validation is None:
        return _make_check_item(
            check_type=LAYOUT_STRUCTURE,
            passed=True,
            severity="low",
            detail="Not assessed (no structural validation result available).",
        )

    reading_ok = structural_validation.get("readingOrderConsistent", True)
    if reading_ok is not False:
        return _make_check_item(
            check_type=LAYOUT_STRUCTURE,
            passed=True,
            severity="low",
            detail="Reading order appears consistent between extraction passes.",
        )

    docling_lines = len(docling_text.splitlines()) if docling_text else 0
    textract_lines = len(textract_text.splitlines()) if textract_text else 0
    if docling_lines > 0 and textract_lines > 0:
        ratio = min(docling_lines, textract_lines) / max(docling_lines, textract_lines)
    else:
        ratio = 1.0

    if ratio < 0.5:
        severity = "high"
        detail = (
            f"Two-column / table layout suspected — one extraction pass "
            f"recovered significantly less text than the other "
            f"(line ratio {ratio:.2f}), which is the same signal the "
            f"merged validation already flagged."
        )
    else:
        severity = "medium"
        detail = (
            "Reading order not preserved between extraction passes — "
            "layout may stress common ATS parsers."
        )

    return _make_check_item(
        check_type=LAYOUT_STRUCTURE,
        passed=False,
        severity=severity,
        detail=detail,
    )


def check_contact_info_location(*, structured_payload: dict | None,
                                canonical_text: str) -> tuple[dict, bool]:
    """Whether email and phone are present AND near the top of the
    document.  Returns (check_item, contact_info_parseable).

    Severity: always **high** per the spec when info is missing or buried.
    """
    basics = (structured_payload or {}).get("basics") or {}

    email = (basics.get("email") or "").strip()
    phone = (basics.get("phone") or "").strip()

    parseable = bool(email) and bool(phone)

    if not parseable:
        missing = [k for k, v in [("email", email), ("phone", phone)] if not v]
        return (
            _make_check_item(
                check_type=CONTACT_INFO_LOCATION,
                passed=False,
                severity="high",
                detail=(
                    f"Contact info not fully extractable — "
                    f"missing: {', '.join(missing)}. "
                    f"ATS parsers require clean contact info to match."
                ),
            ),
            False,
        )

    lines = canonical_text.splitlines()
    threshold = max(1, int(len(lines) * CONTACT_INFO_TOP_DOC_FRACTION))
    top_region = "\n".join(lines[:threshold]).lower()

    email_near_top = email.lower() in top_region
    phone_parts = phone.strip().split()
    phone_last = phone_parts[-1] if phone_parts else phone
    phone_near_top = any(p in top_region for p in (phone, phone_last))

    if email_near_top and phone_near_top:
        return (
            _make_check_item(
                check_type=CONTACT_INFO_LOCATION,
                passed=True,
                severity="low",
                detail="Contact info found in conventional top-of-document position.",
            ),
            True,
        )

    buried = []
    if not email_near_top:
        buried.append("email")
    if not phone_near_top:
        buried.append("phone")
    return (
        _make_check_item(
            check_type=CONTACT_INFO_LOCATION,
            passed=False,
            severity="high",
            detail=(
                f"Contact info found but not near the top of the document "
                f"({', '.join(buried)} not in first "
                f"{CONTACT_INFO_TOP_DOC_FRACTION:.0%} of lines). Many "
                f"ATS parsers skip headers, footers, or late-appearing "
                f"contact blocks."
            ),
        ),
        True,
    )


def check_non_standard_characters(*, canonical_text: str) -> dict:
    """Unicode characters, symbols-as-bullets, or decorative fonts that
    can render as garbage.  Severity: **low** per the spec.
    """
    matches = _NON_STANDARD_CHAR_RE.findall(canonical_text)
    if not matches:
        return _make_check_item(
            check_type=NON_STANDARD_CHARACTERS,
            passed=True,
            severity="low",
            detail="No non-standard characters detected.",
        )

    unique = sorted(set(matches))
    sample_repr = [f"U+{ord(c):04X}" for c in unique[:8]]
    return _make_check_item(
        check_type=NON_STANDARD_CHARACTERS,
        passed=False,
        severity="low",
        detail=(
            f"Non-standard characters found ({len(matches)} occurrences): "
            f"{', '.join(sample_repr)} — these may render as garbage or "
            f"be dropped by ATS parsers."
        ),
    )



def check_section_heading_recognizability(*,
                                          structured_payload: dict | None
                                          ) -> dict:
    """Whether CV section headings mapped to a recognised canonical type.
    If this system's parser needed a fallback to unknown, a stricter ATS
    parser likely will too.  Severity: **medium** per the spec.
    """
    if structured_payload is None:
        return _make_check_item(
            check_type=SECTION_HEADING_RECOGNIZABILITY,
            passed=True,
            severity="low",
            detail="Not assessed (no structured profile available).",
        )

    from app.extraction.heading_canonicalizer import (
        canonicalize_heading, UNKNOWN,
    )

    heading_names = structured_payload.get("heading_names") or []
    if not heading_names:
        # Derive from raw_heading fields on experience/education items
        unknown_headings: list[str] = []
        for key in ("workExperience", "education", "skills",
                    "certifications", "projects"):
            items = structured_payload.get(key)
            if isinstance(items, list):
                for item in items:
                    raw = (item or {}).get("raw_heading")
                    if raw:
                        canonical, _conf = canonicalize_heading(raw)
                        if canonical == UNKNOWN:
                            unknown_headings.append(raw)

        if unknown_headings:
            return _make_check_item(
                check_type=SECTION_HEADING_RECOGNIZABILITY,
                passed=False,
                severity="medium",
                detail=(
                    f"Section headings could not be canonicalised — "
                    f"a stricter ATS parser likely won't recognise "
                    f"them either. Affected: "
                    f"{', '.join(unknown_headings[:5])}."
                ),
            )
        return _make_check_item(
            check_type=SECTION_HEADING_RECOGNIZABILITY,
            passed=True,
            severity="medium",
            detail="All section headings map to recognised canonical types.",
        )

    # Explicit heading_names present
    unknown_headings: list[str] = []
    for heading in heading_names:
        canonical, _conf = canonicalize_heading(heading)
        if canonical == UNKNOWN:
            unknown_headings.append(heading)

    if unknown_headings:
        return _make_check_item(
            check_type=SECTION_HEADING_RECOGNIZABILITY,
            passed=False,
            severity="medium",
            detail=(
                f"Section headings could not be canonicalised — "
                f"a stricter ATS parser likely won't recognise "
                f"them either. Affected: "
                f"{', '.join(unknown_headings[:5])}."
            ),
        )

    return _make_check_item(
        check_type=SECTION_HEADING_RECOGNIZABILITY,
        passed=True,
        severity="medium",
        detail="All section headings map to recognised canonical types.",
    )



def check_file_format_signals(*, mime_type: str,
                              merge_strategy_metadata: dict | None = None
                              ) -> dict:
    """File-format signals that correlate with poor ATS extraction.
    Severity: **low** per the spec.
    """
    if not mime_type:
        return _make_check_item(
            check_type=FILE_FORMAT_SIGNALS,
            passed=True,
            severity="low",
            detail="Not assessed (no mime type available).",
        )

    signals: list[str] = []
    if merge_strategy_metadata:
        for key in ("pdf_producer", "xmp:CreatorTool", "producer"):
            tool = merge_strategy_metadata.get(key)
            if isinstance(tool, str):
                tool_lower = tool.lower()
                for marker in _PROBLEMATIC_PDF_PRODUCERS:
                    if marker.lower() in tool_lower:
                        signals.append(f"PDF produced by {tool}")
                        break

    if signals:
        return _make_check_item(
            check_type=FILE_FORMAT_SIGNALS,
            passed=False,
            severity="low",
            detail=(
                f"File format signals that may affect ATS parsing: "
                f"{'; '.join(signals)}."
            ),
        )

    return _make_check_item(
        check_type=FILE_FORMAT_SIGNALS,
        passed=True,
        severity="low",
        detail="No problematic file-format signals detected.",
    )


# ──────────────────────────────────────────────────────────────────────
# Composite scorer
# ──────────────────────────────────────────────────────────────────────


def run_ats_check(
    *,
    canonical_text: str = "",
    docling_text: str = "",
    textract_text: str = "",
    ocr_used: bool = False,
    structural_validation: dict | None = None,
    structured_payload: dict | None = None,
    mime_type: str = "",
    merge_strategy_metadata: dict | None = None,
) -> AtsCheckResult:
    """Run all six ATS-readiness checks and compose the overall score.

    Args:
        canonical_text: merged canonical text from cv_raw_text.
        docling_text: Docling pass extracted_text.
        textract_text: Textract pass extracted_text.
        ocr_used: whether the document is a scanned original.
        structural_validation: structural_validation_result dict.
        structured_payload: cv_profile_versions.structured_payload.
        mime_type: cv_files.mime_type.
        merge_strategy_metadata: cv_raw_text.merge_strategy_metadata.

    Returns:
        AtsCheckResult with overall_score (0-1), itemised checks, and
        contact_info_parseable.
    """
    checks: list[dict] = []
    contact_info_parseable = False

    # 1. text_in_image
    checks.append(check_text_in_image(
        docling_text=docling_text, textract_text=textract_text,
        ocr_used=ocr_used,
    ))

    # 2. layout_structure
    checks.append(check_layout_structure(
        structural_validation=structural_validation,
        docling_text=docling_text,
        textract_text=textract_text,
    ))

    # 3. contact_info_location
    ci_check, contact_info_parseable = check_contact_info_location(
        structured_payload=structured_payload,
        canonical_text=canonical_text,
    )
    checks.append(ci_check)

    # 4. non_standard_characters
    checks.append(check_non_standard_characters(
        canonical_text=canonical_text,
    ))

    # 5. section_heading_recognizability
    checks.append(check_section_heading_recognizability(
        structured_payload=structured_payload,
    ))

    # 6. file_format_signals
    checks.append(check_file_format_signals(
        mime_type=mime_type,
        merge_strategy_metadata=merge_strategy_metadata,
    ))

    # ── Score ────────────────────────────────────────────────────────
    score = 1.0
    for check in checks:
        if not check["passed"]:
            severity = check.get("severity", "low")
            score -= SEVERITY_DEDUCTIONS.get(severity, 0.0)
    score = round(max(score, 0.0), 2)

    # Apply contact-info unparseable cap *after* the additive deductions
    if not contact_info_parseable:
        score = min(score, CONTACT_INFO_UNPARSEABLE_CAP)

    return AtsCheckResult(
        overall_score=score,
        checks=checks,
        contact_info_parseable=contact_info_parseable,
    )
