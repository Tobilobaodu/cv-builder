"""Pure rendering functions for Sprint 5 document exports.

No DB/Celery session handling here — same "pure orchestration over
plain Python objects, testable with fake data" discipline as
tailored_cv_generation.py/cover_letter_generation.py.
worker_jobs.py::process_export_docx loads the real rows and calls these.

Honest, known gap this module has to render around, not fix: CvProfileVersion.
structured_payload["basics"]["name"/"email"/"phone"/"location"] are all
hardcoded None by worker_jobs.py::process_cv_parse today — this CV
parser has never extracted a candidate's name or contact details from
the document text, only the summary. resolve_candidate_name/
resolve_contact_line below degrade gracefully (omit the header line
entirely, never fabricate a name) rather than pretend this data exists;
an account-owned export falls back to the user's login email as the one
real contact detail actually available. This is a pre-existing
extraction gap, not something Sprint 5 introduces or is scoped to fix.
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from docxtpl import DocxTemplate

_BULLET_PREFIX_RE = re.compile(r"^[\s•\-\*•]+")

_SECTION_HEADINGS = {
    "summary": "Professional Summary",
    "education": "Education",
    "experience": "Experience",
    "projects": "Projects",
    "skills": "Skills",
    "body": "Tailored CV",
}


def resolve_candidate_name(basics: dict | None) -> str | None:
    return (basics or {}).get("name") or None


def resolve_contact_line(basics: dict | None, *, fallback_email: str | None = None) -> str | None:
    basics = basics or {}
    email = basics.get("email") or fallback_email
    parts = [p for p in [email, basics.get("phone"), basics.get("location")] if p]
    return " | ".join(parts) if parts else None


def _split_bullets(content_text: str) -> list[str]:
    """A generated section's content_text may be one bullet or several,
    newline-separated, sometimes with a leading bullet glyph the model
    added on its own — normalize to a clean list of non-empty bullet
    lines. Falls back to the whole text as a single bullet when there's
    no newline structure at all, rather than dropping content."""
    lines = [_BULLET_PREFIX_RE.sub("", line).strip() for line in content_text.splitlines()]
    lines = [line for line in lines if line]
    if lines:
        return lines
    stripped = content_text.strip()
    return [stripped] if stripped else []


def _format_date(value: datetime | None) -> str | None:
    return value.strftime("%b %Y") if value else None


def _format_date_range(start: datetime | None, end: datetime | None, current: bool) -> str | None:
    start_s = _format_date(start)
    end_s = "Present" if current else _format_date(end)
    if start_s and end_s:
        return f"{start_s} – {end_s}"
    return start_s or end_s


def _format_experience_header(item) -> str | None:
    """None when source_item_id didn't resolve (row deleted since
    generation, or the section predates this column) — the renderer
    still shows the section's bullets, just without a role/company
    header, never guesses one."""
    if item is None:
        return None
    role_company = " — ".join(p for p in [item.title, item.company] if p)
    if not role_company:
        return None
    date_range = _format_date_range(item.start_date, item.end_date, bool(getattr(item, "current", False)))
    return f"{role_company} ({date_range})" if date_range else role_company


def _format_project_header(item) -> str | None:
    if item is None or not item.name:
        return None
    return item.name


def build_cv_docx_context(
    *,
    candidate_name: str | None,
    contact_line: str | None,
    sections: list,
    experience_by_id: dict,
    project_by_id: dict,
) -> dict:
    """sections: TailoredCvSection rows (or any object exposing the same
    attributes), any order — grouped here by section_type into one
    heading block per type, with experience/project sections further
    grouped into per-role/per-project entries under that one heading
    (content_json's own flat one-row-per-role shape doesn't have this
    grouping, so a real renderer needs to build it)."""
    ordered = sorted(sections, key=lambda s: (s.order_index if s.order_index is not None else 0))

    blocks: list[dict] = []
    blocks_by_type: dict[str, dict] = {}

    for section in ordered:
        kind = section.section_type
        heading = _SECTION_HEADINGS.get(kind, kind.replace("_", " ").title())

        if kind in ("experience", "projects"):
            block = blocks_by_type.get(kind)
            if block is None:
                block = {"kind": kind, "heading": heading, "paragraph": None, "lines": None, "entries": []}
                blocks_by_type[kind] = block
                blocks.append(block)
            if kind == "experience":
                header = _format_experience_header(experience_by_id.get(section.source_item_id))
            else:
                header = _format_project_header(project_by_id.get(section.source_item_id))
            block["entries"].append({"header": header, "bullets": _split_bullets(section.content_text)})
        elif kind == "education":
            lines = [line.strip() for line in section.content_text.splitlines() if line.strip()]
            blocks.append({"kind": kind, "heading": heading, "paragraph": None, "lines": lines, "entries": None})
        elif kind == "body":
            # Single-call rewrite output (tailored_cv_body): a full markdown
            # document. Rendered line-by-line like education — the newlines
            # carry the document's structure, and the single-paragraph
            # fallback below would collapse them into one blob.
            lines = [line.strip() for line in section.content_text.splitlines() if line.strip()]
            blocks.append({"kind": kind, "heading": heading, "paragraph": None, "lines": lines, "entries": None})
        else:
            # summary, skills, or any future single-paragraph section type
            blocks.append({
                "kind": kind, "heading": heading,
                "paragraph": section.content_text.strip(), "lines": None, "entries": None,
            })

    return {"candidate_name": candidate_name, "contact_line": contact_line, "blocks": blocks}


def build_cover_letter_docx_context(
    *,
    candidate_name: str | None,
    contact_line: str | None,
    sent_date: str,
    employer_name: str | None,
    body_text: str,
) -> dict:
    body_paragraphs = [p.strip() for p in body_text.split("\n\n") if p.strip()]
    if not body_paragraphs and body_text.strip():
        body_paragraphs = [body_text.strip()]
    return {
        "candidate_name": candidate_name,
        "contact_line": contact_line,
        "sent_date": sent_date,
        "employer_name": employer_name,
        "body_paragraphs": body_paragraphs,
    }


def render_docx(template_path: Path, context: dict) -> bytes:
    tpl = DocxTemplate(str(template_path))
    tpl.render(context)
    buf = BytesIO()
    tpl.save(buf)
    return buf.getvalue()


def build_application_pack_zip(*, cv_docx: bytes, cover_letter_docx: bytes) -> bytes:
    """Two independently well-formed files, not a merged document —
    merging risks breaking each document's own ATS structure, and this
    codebase's whole design ethos is 'don't invent structure that isn't
    real.'"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tailored_cv.docx", cv_docx)
        zf.writestr("cover_letter.docx", cover_letter_docx)
    return buf.getvalue()
