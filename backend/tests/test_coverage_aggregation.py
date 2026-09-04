"""Pure-function tests for app/services/coverage_aggregation.py — no DB,
no Celery, no docling dependency. Product Extension #2
(11-product-extensions.md §2)."""

from types import SimpleNamespace

from app.services import coverage_aggregation


def _ev(requirement_text, support_level):
    return SimpleNamespace(requirement_text=requirement_text, support_level=support_level)


class TestClusteringKey:
    def test_case_and_punctuation_fold_via_normalize_skill_fallback(self):
        """A nonsense phrase guaranteed not to resolve via the real ESCO/
        O*NET taxonomy exercises the normalize_skill() fallback path
        deterministically, without depending on the committed index's
        actual data content."""
        key_a = coverage_aggregation.clustering_key("Xyzzyworks Integration")
        key_b = coverage_aggregation.clustering_key("xyzzyworks integration")
        assert key_a == key_b

    def test_prefers_taxonomy_uri_when_canonicalize_resolves(self, monkeypatch):
        """Two differently-worded phrases that resolve to the same
        taxonomy concept must cluster together — the whole point of
        reusing skills_index over a bare string fold."""
        fake_match_a = SimpleNamespace(canonical_label="User experience design", uri="esco:1234", matched_text="UX")
        fake_match_b = SimpleNamespace(canonical_label="User experience design", uri="esco:1234", matched_text="User Experience")

        def fake_canonicalize(term):
            return fake_match_a if term == "UX" else fake_match_b if term == "User Experience" else None

        monkeypatch.setattr(coverage_aggregation.skills_index, "canonicalize", fake_canonicalize)

        assert coverage_aggregation.clustering_key("UX") == coverage_aggregation.clustering_key("User Experience")
        assert coverage_aggregation.clustering_key("UX") == "esco:1234"


class TestAggregateGaps:
    def test_only_unsupported_contradictory_unclear_surface(self):
        evidence = {
            "jp1": [
                _ev("Python", "supported"),
                _ev("Kubernetes", "unsupported"),
                _ev("Docker", "partially_supported"),
                _ev("Terraform", "contradictory"),
                _ev("AWS", "unclear"),
            ],
        }
        result = coverage_aggregation.aggregate_gaps(evidence, total_posts=1)
        clusters = {r["requirement_text_cluster"] for r in result}
        assert clusters == {"Kubernetes", "Terraform", "AWS"}

    def test_recurrence_counted_per_distinct_job_post_not_per_evidence_item(self):
        """The same requirement appearing twice in one posting's own
        evidence (e.g. in both required_skills and qualifications) must
        not inflate that posting's contribution to recurrence_count."""
        evidence = {
            "jp1": [_ev("Kubernetes", "unsupported"), _ev("Kubernetes", "unclear")],
        }
        result = coverage_aggregation.aggregate_gaps(evidence, total_posts=1)
        assert len(result) == 1
        assert result[0]["recurrence_count"] == 1

    def test_recurrence_ratio_uses_full_collection_size_as_denominator(self):
        """total_posts is the collection's full nominal size, not just
        the postings that produced usable evidence — a skipped posting
        must genuinely lower the ratio, not be silently excluded from
        the denominator."""
        evidence = {
            "jp1": [_ev("Kubernetes", "unsupported")],
            "jp2": [_ev("Kubernetes", "unsupported")],
            # jp3 skipped (e.g. missing JobPostProfile) — not a key here at all
        }
        result = coverage_aggregation.aggregate_gaps(evidence, total_posts=3)
        assert result[0]["recurrence_count"] == 2
        assert result[0]["recurrence_ratio"] == 2 / 3

    def test_affected_job_post_ids_and_distribution(self):
        evidence = {
            "jp1": [_ev("Kubernetes", "unsupported")],
            "jp2": [_ev("Kubernetes", "contradictory")],
        }
        result = coverage_aggregation.aggregate_gaps(evidence, total_posts=2)
        gap = result[0]
        assert gap["affected_job_post_ids"] == ["jp1", "jp2"]
        assert gap["current_support_level_distribution"] == {"unsupported": 1, "contradictory": 1}

    def test_sorted_by_recurrence_count_descending(self):
        evidence = {
            "jp1": [_ev("Kubernetes", "unsupported"), _ev("Rust", "unsupported")],
            "jp2": [_ev("Kubernetes", "unsupported")],
            "jp3": [_ev("Kubernetes", "unsupported")],
        }
        result = coverage_aggregation.aggregate_gaps(evidence, total_posts=3)
        assert [r["requirement_text_cluster"] for r in result] == ["Kubernetes", "Rust"]
        assert result[0]["recurrence_count"] == 3
        assert result[1]["recurrence_count"] == 1

    def test_empty_evidence_returns_empty_list(self):
        assert coverage_aggregation.aggregate_gaps({}, total_posts=5) == []

    def test_zero_total_posts_returns_empty_list_not_a_division_error(self):
        evidence = {"jp1": [_ev("Kubernetes", "unsupported")]}
        assert coverage_aggregation.aggregate_gaps(evidence, total_posts=0) == []
