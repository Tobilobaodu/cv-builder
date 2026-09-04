"""skills_index.py — the ESCO taxonomy lookup/normalization module.

Tests run against a small synthetic index (not the real 5MB ESCO data)
so they're deterministic and independent of what future ESCO releases
change — the real data is exercised separately via the live walkthrough
documented in the roadmap doc.
"""

import json

import pytest

from app.extraction import skills_index


@pytest.fixture(autouse=True)
def _synthetic_index(tmp_path, monkeypatch):
    """Point the module at a small, known index and clear its caches
    before and after each test so tests don't leak state between runs."""
    data = {
        "user experience design": {
            "label": "user experience design",
            "uri": "http://example.org/skill/ux-design",
            "alt_labels": ["ux design", "ux"],
        },
        "usability engineering": {
            "label": "usability engineering",
            "uri": "http://example.org/skill/usability-engineering",
            "alt_labels": ["usability testing"],
        },
        "information architecture": {
            "label": "information architecture",
            "uri": "http://example.org/skill/information-architecture",
            "alt_labels": [],
        },
        "python computer programming": {
            "label": "python computer programming",
            "uri": "http://example.org/skill/python",
            "alt_labels": ["python programming"],
        },
        "design": {
            "label": "design",
            "uri": "http://example.org/skill/design-generic",
            "alt_labels": ["craft"],
        },
    }
    index_path = tmp_path / "skills_index.json"
    index_path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(skills_index, "_INDEX_PATH", index_path)
    skills_index._load_index.cache_clear()
    skills_index._load_phrase_lookup.cache_clear()
    skills_index._load_canonical_lookup.cache_clear()
    yield
    skills_index._load_index.cache_clear()
    skills_index._load_phrase_lookup.cache_clear()
    skills_index._load_canonical_lookup.cache_clear()


def test_normalize_skill_strips_punctuation_and_case():
    assert skills_index.normalize_skill("UX Design!") == "ux design"
    assert skills_index.normalize_skill("  Python   Programming  ") == "python programming"


class TestCanonicalize:
    def test_exact_canonical_label(self):
        result = skills_index.canonicalize("User Experience Design")
        assert result is not None
        assert result.canonical_label == "user experience design"
        assert result.uri == "http://example.org/skill/ux-design"

    def test_alt_label_resolves_to_canonical(self):
        result = skills_index.canonicalize("UX")
        assert result is not None
        assert result.canonical_label == "user experience design"

    def test_synonym_resolves_to_same_uri_as_canonical(self):
        canonical = skills_index.canonicalize("usability engineering")
        synonym = skills_index.canonicalize("usability testing")
        assert canonical is not None and synonym is not None
        assert canonical.uri == synonym.uri

    def test_no_match_returns_none(self):
        assert skills_index.canonicalize("underwater basket weaving") is None

    def test_empty_string_returns_none(self):
        assert skills_index.canonicalize("") is None
        assert skills_index.canonicalize("   ") is None


class TestMatchTerms:
    def test_multi_word_phrase_is_matched(self):
        text = "We need strong information architecture skills."
        matches = skills_index.match_terms(text)
        labels = [m.canonical_label for m in matches]
        assert "information architecture" in labels

    def test_single_word_terms_are_excluded_by_default(self):
        """Confirmed against the real data: single-word ESCO labels are
        disproportionately noisy (a generic word like 'design' resolving
        to an unrelated narrow concept). min_words=2 default excludes
        them."""
        text = "Great design and craft are essential here."
        matches = skills_index.match_terms(text)
        labels = [m.canonical_label for m in matches]
        assert "design" not in labels

    def test_longest_phrase_wins_over_shorter_overlapping_candidate(self):
        text = "Experience with python computer programming is required."
        matches = skills_index.match_terms(text)
        labels = [m.canonical_label for m in matches]
        assert "python computer programming" in labels
        # Only one match for this span, not a shorter overlapping one too.
        assert labels.count("python computer programming") == 1

    def test_word_boundary_safety(self):
        """A phrase must not match as a substring inside a longer,
        unrelated word."""
        text = "megausabilityengineeringmega"
        matches = skills_index.match_terms(text)
        assert matches == []

    def test_no_matches_returns_empty_list(self):
        assert skills_index.match_terms("completely unrelated prose text") == []

    def test_empty_text_returns_empty_list(self):
        assert skills_index.match_terms("") == []


class TestLiteralCoverage:
    """Q1 — deterministic, synonym-blind keyword-in-text check. Does not
    touch the ESCO index at all (pure normalize_skill substring test), so
    the synthetic-index fixture above is incidental here, not exercised."""

    def test_all_keywords_present(self):
        result = skills_index.literal_coverage(
            "Built APIs in Python and Go, deployed with Docker.",
            ["Python", "Docker"],
        )
        assert result["coverage"] == 1.0
        assert result["present"] == ["Python", "Docker"]
        assert result["absent"] == []

    def test_partial_coverage(self):
        result = skills_index.literal_coverage(
            "Built APIs in Python.", ["Python", "Kubernetes"]
        )
        assert result["coverage"] == 0.5
        assert result["present"] == ["Python"]
        assert result["absent"] == ["Kubernetes"]

    def test_no_keywords_present(self):
        result = skills_index.literal_coverage("Unrelated CV text.", ["Rust", "Go"])
        assert result["coverage"] == 0.0
        assert result["present"] == []
        assert result["absent"] == ["Rust", "Go"]

    def test_no_priority_keywords_is_zero_not_a_crash(self):
        result = skills_index.literal_coverage("Some CV text.", [])
        assert result == {"coverage": 0.0, "present": [], "absent": []}

    def test_case_and_whitespace_insensitive(self):
        result = skills_index.literal_coverage(
            "Experience with  POSTGRESQL  databases.", ["PostgreSQL"]
        )
        assert result["coverage"] == 1.0

    def test_synonym_is_not_credited_unlike_the_llm_score(self):
        # The whole point of this check vs atsScore: no synonym handling.
        # "JS" is a common alias for JavaScript, but this is a literal
        # substring test, not a taxonomy lookup.
        result = skills_index.literal_coverage(
            "Five years of JavaScript development.", ["JS"]
        )
        assert result["coverage"] == 0.0
