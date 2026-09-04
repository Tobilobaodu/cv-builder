"""Registry of available export document templates.

Config-as-code, not a DB table — this is presentation metadata (which
.docx layout to render into), not user data, same reasoning as the
job_post_llm_enrichment_* style config-as-code settings in
app/core/config.py. Backs GET /exports/templates for a frontend picker.

Cover letters get exactly one fixed template, not a picker — the
"multiple ATS-ready layouts" request is specifically about
machine-parseability, a CV concern; a cover letter is read by a human,
not structurally parsed by an ATS.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "exports"

CV_TEMPLATES: dict[str, dict[str, str]] = {
    "standard": {
        "name": "Standard",
        "description": "Generous spacing and larger headings — a good default for shorter CVs.",
    },
    "compact": {
        "name": "Compact",
        "description": "Tighter spacing and smaller text, fitting more onto fewer pages — for longer CVs.",
    },
}

DEFAULT_CV_TEMPLATE_ID = "standard"
COVER_LETTER_TEMPLATE_FILE = TEMPLATE_DIR / "cover_letter.docx"


def resolve_cv_template_id(template_id: str | None) -> str:
    """Never rejects an export over an unrecognized template id — falls
    back to the default rather than failing the whole export over a
    presentation-only choice."""
    if template_id and template_id in CV_TEMPLATES:
        return template_id
    return DEFAULT_CV_TEMPLATE_ID


def cv_template_path(template_id: str) -> Path:
    return TEMPLATE_DIR / f"cv_{template_id}.docx"


def list_templates() -> list[dict[str, str]]:
    return [
        {"id": template_id, "name": meta["name"], "description": meta["description"]}
        for template_id, meta in CV_TEMPLATES.items()
    ]
