"""O*NET Software Skills merged into the same index as ESCO
(scripts/ingest_onet.py) — the secondary source per the original M2
plan. Runs against the real, committed merged index (not a synthetic
fixture): specifically verifying the merge itself, complementary to
test_skills_index.py's behavior tests against synthetic data.

Why single-word matching is allowed here via canonicalize() but not
through match_terms()'s prose sweep: O*NET's terms are specific product
names ("Figma", "Docker"), not the generic dictionary words that made
ESCO's single-word alt-labels noisy — but auditing all ~900 single-word
O*NET terms for accidental collisions with ordinary English words
wasn't done, so match_terms()'s 2+-word floor is left in place for both
sources rather than assumed safe.
"""

from app.extraction.skills_index import canonicalize, match_terms


def test_onet_single_word_brand_name_canonicalizes():
    """Not reachable via ESCO — confirmed absent from the ESCO-only
    index during development. Only present because O*NET was merged in."""
    result = canonicalize("Figma")
    assert result is not None
    assert result.canonical_label == "Figma"
    assert result.uri.startswith("onet:")


def test_onet_multi_word_term_is_matched_in_prose():
    text = "Experience with Microsoft Access and Google Analytics required."
    matches = match_terms(text)
    labels = [m.canonical_label for m in matches]
    assert "Google Analytics" in labels


def test_esco_and_onet_both_present_in_merged_index():
    """The merge preserves both sources rather than one overwriting the
    other — spot-check one term unique to each."""
    esco_only = canonicalize("information architecture")
    onet_only = canonicalize("Docker")
    assert esco_only is not None
    assert onet_only is not None
    assert esco_only.uri != onet_only.uri
