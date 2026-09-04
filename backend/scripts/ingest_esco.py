"""One-time (re-runnable) ingest of the ESCO skills/knowledge taxonomy into
a compact, committed lookup index used by job_post_parser.py and
match_engine.py for domain-agnostic requirement extraction and matching.

Source: ESCO (European Skills, Competences, Qualifications and
Occupations), European Commission, https://esco.ec.europa.eu — reused
under Commission Decision 2011/833/EU (not CC BY 4.0 — verified directly
against ESCO's own FAQ, not assumed), free for any purpose including
commercial use, attribution required (see backend/README.md).

Input: the full ESCO XML classification export (v1.2.1 at the time this
was written), downloaded manually from
https://esco.ec.europa.eu/en/use-esco/download (email-gated — no way to
automate the download itself). This script only processes an already
-downloaded file; it does not fetch anything over the network.

Output: app/data/skills_index.json — {canonical_lower: {label, uri,
alt_labels: [...]}} for every released English-labeled skill/knowledge
concept. Committed to the repo so every environment (including the
network-isolated cv_parse/ats_check workers) has identical, offline data
with no runtime dependency on ESCO's servers.

Usage:
    python scripts/ingest_esco.py <path-to-esco-vX.Y.Z.xml> [output_path]
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_MODEL_NS = "http://data.europa.eu/esco/model#"
_SKOS_NS = "http://www.w3.org/2004/02/skos/core#"

_SKILL_TAG = f"{{{_MODEL_NS}}}skill"
_STATUS_TAG = f"{{{_MODEL_NS}}}status"
_SKOS_PREF_LABEL_TAG = f"{{{_SKOS_NS}}}prefLabel"
_SKOS_ALT_LABEL_TAG = f"{{{_SKOS_NS}}}altLabel"

_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "app" / "data" / "skills_index.json"


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the same key
    shape used for lookups at match time, so index keys and query terms
    are directly comparable."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _extract_skill(elem: ET.Element) -> dict | None:
    """Pull the fields we need from one <skill> element. Returns None for
    concepts with no English preferred label (a small minority) or
    non-released status (deprecated/draft concepts we don't want feeding
    live matching)."""
    status = elem.findtext(_STATUS_TAG)
    if status is not None and status != "released":
        return None

    pref_label = None
    alt_labels: list[str] = []
    for child in elem:
        if child.tag == _SKOS_PREF_LABEL_TAG and child.get("language") == "en":
            if child.text and child.text.strip():
                pref_label = child.text.strip()
        elif child.tag == _SKOS_ALT_LABEL_TAG and child.get("language") == "en":
            if child.text and child.text.strip():
                alt_labels.append(child.text.strip())

    if not pref_label:
        return None

    return {
        "label": pref_label,
        "uri": elem.get("uri"),
        "alt_labels": sorted(set(alt_labels)),
        "source": "esco",
    }


def ingest(xml_path: Path) -> dict[str, dict]:
    """Stream-parse the ESCO XML dump. Uses iterparse rather than loading
    the full ~700MB tree into memory — clears each <skill> element's
    children immediately after extracting what's needed, since that's
    where the real memory (hundreds of multilingual label sub-elements
    per skill) lives; the emptied element shells left under their parent
    are negligible (tens of thousands of near-empty tags, not a real
    footprint)."""
    index: dict[str, dict] = {}
    skill_count = 0
    skipped_count = 0

    context = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in context:
        if elem.tag != _SKILL_TAG:
            continue

        skill_count += 1
        entry = _extract_skill(elem)
        elem.clear()

        if entry is None:
            skipped_count += 1
            continue

        key = _normalize(entry["label"])
        if not key:
            skipped_count += 1
            continue

        if key in index:
            # Same normalized label from two concepts (rare) — keep the
            # first, merge alt labels rather than silently dropping data.
            existing = index[key]
            existing["alt_labels"] = sorted(
                set(existing["alt_labels"]) | set(entry["alt_labels"])
            )
        else:
            index[key] = entry

    print(
        f"Processed {skill_count} <skill> elements, "
        f"{skipped_count} skipped (no English label or not released), "
        f"{len(index)} unique canonical entries.",
        file=sys.stderr,
    )
    return index


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <path-to-esco.xml> [output_path]", file=sys.stderr)
        raise SystemExit(1)

    xml_path = Path(sys.argv[1])
    if not xml_path.exists():
        print(f"Input file not found: {xml_path}", file=sys.stderr)
        raise SystemExit(1)

    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_OUTPUT
    output_path.parent.mkdir(parents=True, exist_ok=True)

    index = ingest(xml_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {len(index)} entries to {output_path} ({size_mb:.1f} MB).", file=sys.stderr)


if __name__ == "__main__":
    main()
