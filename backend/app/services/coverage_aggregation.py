"""Pure aggregation functions for multi-job-post coverage reporting
(Sprint 5 / Product Extension #2, 11-product-extensions.md §2).

A read/aggregation layer over already-computed match_runs/
match_evidence_items — no new matching logic, no new evidence-binding
surface, no LLM call. worker_jobs.py's process_coverage_report loads
the real rows (reusing an existing completed MatchRun per job post where
one exists, or running match_engine.run_match() fresh where one
doesn't) and calls these pure functions to do the actual grouping/
ranking, same "pure orchestration over plain Python objects, testable
without a DB" discipline as the generation-side services.
"""

from __future__ import annotations

from app.extraction import skills_index

_ELIGIBLE_SUPPORT_LEVELS = frozenset({"unsupported", "contradictory", "unclear"})


def clustering_key(requirement_text: str) -> str:
    """Light normalization only, per the spec's own explicit scoping
    ('light normalization... full semantic clustering deferred') — reuses
    the ESCO/O*NET taxonomy lookup built for M2 rather than a bespoke
    normalizer, so 'Kubernetes' and 'kubernetes experience' collapse to
    the same cluster when they resolve to the same taxonomy concept, and
    falls back to a case/punctuation fold when nothing resolves."""
    match = skills_index.canonicalize(requirement_text)
    if match is not None:
        return match.uri
    return skills_index.normalize_skill(requirement_text)


def aggregate_gaps(evidence_by_job_post: dict[str, list], total_posts: int) -> list[dict]:
    """evidence_by_job_post: job_post_id -> list of objects exposing
    .requirement_text/.support_level (MatchEvidenceItem rows, or
    anything with the same shape). total_posts: the full nominal
    collection size, not just the postings that produced usable evidence
    — a posting skipped for missing data still counts in the ratio's
    denominator, so a lower recurrence_ratio is an honest signal rather
    than silently propped up by shrinking the denominator to match.

    Only unsupported/contradictory/unclear support levels ever surface,
    per the spec's explicit framing — this is a *gap* report.

    Recurrence is counted per distinct job post, not per raw evidence
    item: a requirement appearing twice in one posting's own evidence
    (e.g. present in both required_skills and qualifications) must not
    inflate that posting's contribution to recurrence_count.
    """
    if total_posts <= 0:
        return []

    clusters: dict[str, dict] = {}

    for job_post_id, items in evidence_by_job_post.items():
        seen_this_post: dict[str, tuple[str, str]] = {}
        for item in items:
            if item.support_level not in _ELIGIBLE_SUPPORT_LEVELS:
                continue
            key = clustering_key(item.requirement_text)
            if not key:
                continue
            seen_this_post.setdefault(key, (item.requirement_text, item.support_level))

        for key, (display_text, support_level) in seen_this_post.items():
            cluster = clusters.setdefault(key, {"display_text": display_text, "job_posts": {}})
            cluster["job_posts"][job_post_id] = support_level

    results = []
    for cluster in clusters.values():
        job_post_ids = sorted(cluster["job_posts"].keys())
        distribution: dict[str, int] = {}
        for level in cluster["job_posts"].values():
            distribution[level] = distribution.get(level, 0) + 1
        recurrence_count = len(job_post_ids)
        results.append({
            "requirement_text_cluster": cluster["display_text"],
            "recurrence_count": recurrence_count,
            "recurrence_ratio": recurrence_count / total_posts,
            "affected_job_post_ids": job_post_ids,
            "current_support_level_distribution": distribution,
        })

    results.sort(key=lambda r: r["recurrence_count"], reverse=True)
    return results
