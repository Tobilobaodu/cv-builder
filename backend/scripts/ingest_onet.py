"""Merge O*NET's Software Skills data into the skills lookup index built
by ingest_esco.py — the secondary source per the original M2 plan.

Source: O*NET 30.3, "Software Skills" (formerly Technology Skills),
https://www.onetcenter.org/database.html — downloads directly, no
registration required (unlike ESCO). Used under CC BY 4.0: this service
credits "the O*NET 30.3 Database and the U.S. Department of Labor,
Employment and Training Administration" as the original source (see
backend/README.md); "O*NET" is used here as required — as an adjective,
not a noun or verb.

Why this source specifically, not O*NET's "Essential Skills" file: that
file covers ~35 broad competency dimensions ("Reading Comprehension",
"Active Listening") with numeric importance/level ratings per
occupation — abstract rating data, not skill *terms* a CV or job post
would ever mention by name. Software Skills' "Workplace Example" column
is real, specific tool/software names ("Figma", "Docker", "Microsoft
Access") tied to occupations — this is exactly the brand/tool-name
coverage ESCO's vendor-neutral vocabulary is missing (confirmed directly:
"Figma" does not appear anywhere in the ESCO index; it does appear in
O*NET's Software Skills data).

Input: the O*NET "Software Skills" CSV
(https://www.onetcenter.org/dl_files/database/db_30_3_csv/software_skills.csv),
downloaded directly — no manual/email-gated step needed, unlike ESCO.

Usage:
    python scripts/ingest_onet.py <path-to-software_skills.csv> [index_path]
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

_DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "app" / "data" / "skills_index.json"


def _normalize(text: str) -> str:
    """Same normalization rule as ingest_esco.py / skills_index.py —
    keys must be directly comparable across sources."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "unknown"


def extract_onet_terms(csv_path: Path) -> dict[str, dict]:
    """Read the Software Skills CSV and build one entry per unique
    Workplace Example value. O*NET has no stable global URI scheme for
    these examples (unlike ESCO's concept URIs), so a deterministic
    synthetic one is constructed from the normalized label — stable
    across re-runs, not tied to any specific occupation row."""
    index: dict[str, dict] = {}
    skipped = 0

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            example = (row.get("Workplace Example") or "").strip()
            if not example:
                skipped += 1
                continue

            key = _normalize(example)
            if not key:
                skipped += 1
                continue

            if key not in index:
                index[key] = {
                    "label": example,
                    "uri": f"onet:software-skill:{_slugify(key)}",
                    "alt_labels": [],
                    "source": "onet",
                }

    print(
        f"Processed Software Skills rows, {len(index)} unique workplace "
        f"examples, {skipped} skipped (empty).",
        file=sys.stderr,
    )
    return index


def merge(existing: dict[str, dict], additions: dict[str, dict]) -> dict[str, dict]:
    """Merge O*NET entries into the existing (ESCO-built) index. On a
    key collision (same normalized term from both sources), keep the
    existing entry and fold the new one in as an alt-label rather than
    overwriting — both sources' data survives, and canonicalize() still
    resolves either wording to one entry."""
    merged = dict(existing)
    added = 0
    folded = 0

    for key, entry in additions.items():
        if key not in merged:
            merged[key] = entry
            added += 1
        else:
            existing_entry = merged[key]
            if entry["label"] not in existing_entry.get("alt_labels", []) + [existing_entry["label"]]:
                existing_entry["alt_labels"] = sorted(
                    set(existing_entry.get("alt_labels", [])) | {entry["label"]}
                )
                folded += 1

    print(f"Merged: {added} new entries added, {folded} folded into existing ESCO entries.", file=sys.stderr)
    return merged


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <path-to-software_skills.csv> [index_path]", file=sys.stderr)
        raise SystemExit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"Input file not found: {csv_path}", file=sys.stderr)
        raise SystemExit(1)

    index_path = Path(sys.argv[2]) if len(sys.argv) > 2 else _DEFAULT_INDEX
    if not index_path.exists():
        print(f"Index file not found: {index_path} — run ingest_esco.py first.", file=sys.stderr)
        raise SystemExit(1)

    with open(index_path, encoding="utf-8") as f:
        existing = json.load(f)

    onet_terms = extract_onet_terms(csv_path)
    merged = merge(existing, onet_terms)

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    size_mb = index_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {len(merged)} total entries to {index_path} ({size_mb:.1f} MB).", file=sys.stderr)


if __name__ == "__main__":
    main()
