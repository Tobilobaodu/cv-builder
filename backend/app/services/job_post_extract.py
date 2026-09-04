"""Pull just the job out of a fetched careers page.

Stripping HTML was only half the problem. A real posting — the Teamtailor
page this was built against — strips to ~9,000 characters of which the job
itself is maybe a third: cookie-consent copy, career menus, employee login
links, colleague profiles, an "About us" blurb and an applicant-tracking
footer all survive a plain tag strip, and all of it reaches the model as if
it were part of the role.

Three tiers, most precise first:

1. **schema.org/JobPosting JSON-LD.** Google requires it for a posting to
   appear in Google Jobs, so essentially every applicant tracking system
   emits it — Teamtailor, Greenhouse, Lever, Workable, SmartRecruiters,
   Ashby. It is the site's own machine-readable copy of the job: title,
   employer, location, employment type and the description, with none of
   the page furniture. When it is there, nothing else comes close.
2. **The main/article subtree.** For hand-built careers pages with no
   structured data but sane semantics.
3. **The whole page.** What we did before, with page furniture dropped.

Each tier is only accepted if it yields a plausible amount of text, so a
site that emits an empty JSON-LD stub falls through rather than replacing a
good extraction with a bad one.
"""

from __future__ import annotations

import html as html_module
import json
import re
from typing import Any

from app.services.html_to_text import html_to_text, looks_like_html, tidy_text

_LD_JSON_BLOCK = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

# Below this a tier is treated as having found nothing useful. A real
# posting is thousands of characters; a stub with a title and no
# description is worse than the tier below it.
_MIN_USEFUL_CHARS = 200

# Employment types arrive as schema.org enum tokens (FULL_TIME).
_EMPLOYMENT_TYPE = re.compile(r"[_\-]+")


def _iter_json_ld(raw: str):
    """Yield every object in every ld+json block, flattening @graph."""
    for block in _LD_JSON_BLOCK.findall(raw):
        try:
            parsed = json.loads(block.strip())
        except (ValueError, TypeError):
            # One malformed block must not hide the others.
            continue
        stack = [parsed]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node:
                    stack.append(node["@graph"])
                yield node


def _is_job_posting(node: dict[str, Any]) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, str):
        return node_type.lower() == "jobposting"
    if isinstance(node_type, list):
        return any(str(t).lower() == "jobposting" for t in node_type)
    return False


def _place_to_line(place: Any) -> str:
    """Flatten a schema.org Place/PostalAddress into one readable line."""
    if isinstance(place, list):
        parts = [_place_to_line(p) for p in place]
        return ", ".join(p for p in parts if p)
    if isinstance(place, str):
        return place.strip()
    if not isinstance(place, dict):
        return ""

    address = place.get("address", place)
    if isinstance(address, str):
        return address.strip()
    if not isinstance(address, dict):
        return ""

    ordered = [
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("addressCountry"),
    ]
    if isinstance(ordered[2], dict):
        ordered[2] = ordered[2].get("name")
    return ", ".join(str(p).strip() for p in ordered if p)


def _from_json_ld(raw: str) -> str | None:
    """Compose clean text from a schema.org JobPosting, if the page has one."""
    for node in _iter_json_ld(raw):
        if not _is_job_posting(node):
            continue

        description = node.get("description") or ""
        if not isinstance(description, str):
            continue
        # Teamtailor double-escapes: the JSON string holds &lt;h3&gt;, so it
        # needs unescaping before it can be parsed as the HTML it is.
        description = html_module.unescape(description)
        if looks_like_html(description):
            description = html_to_text(description)
        description = tidy_text(description)
        if len(description) < _MIN_USEFUL_CHARS:
            continue

        header: list[str] = []
        title = node.get("title") or node.get("name")
        if isinstance(title, str) and title.strip():
            header.append(title.strip())

        org = node.get("hiringOrganization")
        org_name = org.get("name") if isinstance(org, dict) else org
        if isinstance(org_name, str) and org_name.strip():
            header.append(org_name.strip())

        location = _place_to_line(node.get("jobLocation"))
        remote = node.get("jobLocationType")
        if isinstance(remote, str) and "telecommute" in remote.lower():
            location = f"{location}, Remote" if location else "Remote"
        if location:
            header.append(location)

        employment = node.get("employmentType")
        if isinstance(employment, list):
            employment = ", ".join(str(e) for e in employment)
        if isinstance(employment, str) and employment.strip():
            header.append(_EMPLOYMENT_TYPE.sub(" ", employment).title())

        return "\n".join(header + ["", description]) if header else description

    return None


def extract_job_text(raw: str) -> str:
    """Reduce a fetched page to the posting itself.

    Non-HTML input (a pasted description, a text/plain response) is returned
    untouched. Every tier is best-effort: the worst case is the previous
    behaviour, never a failed fetch.
    """
    if not raw or not looks_like_html(raw):
        return raw

    structured = _from_json_ld(raw)
    if structured:
        return structured

    main = html_to_text(raw, main_only=True)
    whole = html_to_text(raw)

    # main/article is only better if it actually narrowed things down and
    # still holds a posting's worth of text. Some sites wrap the entire
    # page — chrome included — in <main>.
    if len(main) >= _MIN_USEFUL_CHARS and len(main) < len(whole):
        return main
    return whole
