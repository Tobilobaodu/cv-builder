"""Render the tailored resume Markdown to a print-ready PDF.

The old export path (POST /exports/cv/{draftId} -> docxtpl -> Gotenberg
LibreOffice) cannot serve this flow: it renders from a TailoredCvDraft row
and a structured_payload, and the single-call rewrite creates neither. It
goes Markdown -> HTML -> Chromium instead, which also gives real control
over the print styling rather than inheriting a Word template's.

The Markdown converter here handles only the subset the rewrite prompt is
instructed to emit — ATX headings, dash bullets, bold/italic spans and
paragraphs. That is a deliberate choice over adding a Markdown dependency:
the input is produced by our own prompt against our own template, and the
backend keeps a pinned, audited dependency set. Anything unrecognised
degrades to a paragraph rather than being dropped.

Every piece of text is HTML-escaped before any inline markup is applied,
because the content originates in an uploaded CV and is rendered by a real
browser engine.
"""

from __future__ import annotations

import html as html_module
import re

import httpx

from app.services.gotenberg_client import convert_html_to_pdf

# Generous, but a rewrite is a few thousand characters; anything far past
# this is not a resume.
MAX_MARKDOWN_CHARS = 60_000

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# Print stylesheet. Self-contained by necessity: Gotenberg sits on the
# no_internet network, so a web font or external sheet would silently fail
# to load and the PDF would render in a default serif.
#
# One accent color, used sparingly (a rule under the header block, the
# section-label color + left bar, bullet markers) — everything else stays
# near-black for reading contrast. Still single-column, real selectable
# text, no images/icons/tables: this is a look upgrade, not a structural
# one, so it stays exactly as ATS-safe as the plain version it replaces.
_STYLES = """
:root {
  color-scheme: light;
  --accent: #1B3A57;
  --ink: #1a1a1a;
  --muted: #5b6a75;
  --rule: #d8dee2;
}
* { box-sizing: border-box; }
body {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.48;
  color: var(--ink);
  margin: 0;
}
h1 {
  font-size: 21pt;
  font-weight: 700;
  letter-spacing: 0.3px;
  margin: 0 0 4pt;
  text-transform: uppercase;
  color: var(--ink);
}
/* The contact line right under the name: its own line, then a colored
   rule closes off the header block like a letterhead. */
h1 + p {
  color: var(--muted);
  font-size: 9.5pt;
  margin-bottom: 8pt;
  padding-bottom: 10pt;
  border-bottom: 1.5pt solid var(--accent);
}
h2 {
  font-size: 11pt;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--accent);
  border-left: 2.5pt solid var(--accent);
  padding-left: 8pt;
  margin: 18pt 0 8pt;
}
h3 {
  font-size: 10.5pt;
  font-weight: 700;
  margin: 12pt 0 1pt;
  color: var(--ink);
}
/* Each role's date line: smaller and greyer so the structure reads at a
   glance, matching the contact line's treatment (no rule here though —
   that's reserved for the one-per-document header). */
h3 + p { color: var(--muted); font-size: 9.5pt; margin-bottom: 5pt; }
h4, h5, h6 { font-size: 10.5pt; margin: 9pt 0 1pt; }
p { margin: 0 0 6pt; }
ul { margin: 4pt 0 9pt; padding-left: 14pt; }
li { margin-bottom: 3.5pt; }
li::marker { color: var(--accent); }
a { color: inherit; text-decoration: none; }
/* Never leave a role heading stranded at the foot of a page. */
h2, h3 { break-after: avoid; page-break-after: avoid; }
li, p { break-inside: avoid; page-break-inside: avoid; }
"""


def _inline(text: str) -> str:
    """Escape, then apply the inline markup the prompt uses."""
    out = html_module.escape(text, quote=False)
    out = _LINK.sub(
        lambda m: f'<a href="{html_module.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        out,
    )
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    return out


def markdown_to_html(markdown: str) -> str:
    """Convert the rewrite's Markdown to an HTML fragment.

    Not a general Markdown implementation — see the module docstring for
    why. Unrecognised lines become paragraphs, so nothing is ever lost.
    """
    parts: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()

        if not line:
            close_list()
            continue

        heading = _HEADING.match(line)
        if heading:
            close_list()
            level = min(len(heading.group(1)), 6)
            parts.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            continue

        # A horizontal rule, not a bullet.
        if re.fullmatch(r"(?:---+|___+|\*\*\*+)", line):
            close_list()
            parts.append("<hr />")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_inline(bullet.group(1).strip())}</li>")
            continue

        close_list()
        parts.append(f"<p>{_inline(line)}</p>")

    close_list()
    return "\n".join(parts)


def build_resume_html(markdown: str, *, title: str = "Tailored CV") -> str:
    """Wrap the converted Markdown in a self-contained print document."""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8" />'
        f"<title>{html_module.escape(title, quote=False)}</title>"
        f"<style>{_STYLES}</style></head><body>\n"
        f"{markdown_to_html(markdown)}\n"
        "</body></html>"
    )


class ResumePdfError(RuntimeError):
    """The PDF could not be produced. Message is caller-safe."""


def render_resume_pdf(
    markdown: str,
    *,
    title: str = "Tailored CV",
    client: httpx.Client | None = None,
) -> bytes:
    """Markdown in, PDF bytes out. Raises ResumePdfError on any failure."""
    if not markdown or not markdown.strip():
        raise ResumePdfError("There is no CV content to export.")
    if len(markdown) > MAX_MARKDOWN_CHARS:
        raise ResumePdfError("That CV is too long to export.")

    html = build_resume_html(markdown, title=title)
    try:
        return convert_html_to_pdf(html, client=client)
    except (httpx.HTTPError, OSError) as e:
        raise ResumePdfError(
            "We couldn't build the PDF. Please try again in a moment."
        ) from e
