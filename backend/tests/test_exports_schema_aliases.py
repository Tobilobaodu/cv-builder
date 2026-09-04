"""Dedicated serialization-direction regression test for schemas/export.py
and schemas/coverage.py — the field/alias-inversion bug (camelCase field
name, snake_case alias) has hit this codebase three separate times this
session (cover_letter.py, job_posts.py, matches.py). This file exists so
a fourth instance, in the newest schema files, gets caught immediately
rather than live.
"""

from app.schemas.export import ExportRequestOut, ExportTemplateOut
from app.schemas.coverage import AggregateGapOut, CoverageReportOut, JobPostCollectionOut


def test_export_request_out_serializes_camelcase():
    e = ExportRequestOut(
        id="exp1", status="queued", source_draft_type="tailored_cv",
        file_reference=None, format="docx", template_id="standard",
        downloaded_at=None, derived_from_export_id=None, error_message=None,
        created_at="2026-01-01T00:00:00Z",
    )
    d = e.model_dump(by_alias=True, mode="json")
    assert "sourceDraftType" in d
    assert "fileReference" in d
    assert "templateId" in d
    assert "downloadedAt" in d
    assert "derivedFromExportId" in d
    assert "createdAt" in d
    assert "source_draft_type" not in d, "Should not leak snake_case"
    assert "template_id" not in d, "Should not leak snake_case"
    assert d["sourceDraftType"] == "tailored_cv"
    assert d["templateId"] == "standard"


def test_export_request_out_accepts_camelcase_input():
    e = ExportRequestOut.model_validate({
        "id": "exp1", "status": "queued", "sourceDraftType": "tailored_cv",
        "format": "docx", "createdAt": "2026-01-01T00:00:00Z",
    })
    assert e.source_draft_type == "tailored_cv"


def test_export_template_out_serializes_unchanged_fields():
    t = ExportTemplateOut(id="standard", name="Standard", description="...")
    d = t.model_dump(by_alias=True, mode="json")
    assert d == {"id": "standard", "name": "Standard", "description": "..."}


def test_coverage_report_out_serializes_camelcase():
    r = CoverageReportOut(
        id="cov1", cv_profile_version_id="cvv1", collection_id="col1",
        match_run_ids=["m1", "m2"], status="completed",
        aggregate_gaps=[{
            "requirement_text_cluster": "Kubernetes", "recurrence_count": 3,
            "recurrence_ratio": 0.75, "affected_job_post_ids": ["jp1", "jp2", "jp3"],
            "current_support_level_distribution": {"unsupported": 3},
        }],
        skipped_job_post_ids=None,
        created_at="2026-01-01T00:00:00Z", completed_at=None,
    )
    d = r.model_dump(by_alias=True, mode="json")
    assert "cvProfileVersionId" in d
    assert "collectionId" in d
    assert "matchRunIds" in d
    assert "aggregateGaps" in d
    assert "skippedJobPostIds" in d
    assert "cv_profile_version_id" not in d, "Should not leak snake_case"
    gap = d["aggregateGaps"][0]
    assert "requirementTextCluster" in gap
    assert "recurrenceCount" in gap
    assert "recurrenceRatio" in gap
    assert "affectedJobPostIds" in gap
    assert "currentSupportLevelDistribution" in gap
    assert "requirement_text_cluster" not in gap, "Should not leak snake_case"


def test_job_post_collection_out_serializes_camelcase():
    c = JobPostCollectionOut(
        id="col1", name="My roles", job_post_ids=["jp1", "jp2"],
        created_at="2026-01-01T00:00:00Z", updated_at="2026-01-01T00:00:00Z",
    )
    d = c.model_dump(by_alias=True, mode="json")
    assert "jobPostIds" in d
    assert "createdAt" in d
    assert "updatedAt" in d
    assert "job_post_ids" not in d, "Should not leak snake_case"


def test_aggregate_gap_out_accepts_snake_case_dict_from_jsonb():
    """CoverageReport.aggregate_gaps is stored as plain JSONB dicts with
    snake_case keys (produced by coverage_aggregation.aggregate_gaps()) —
    confirms Pydantic validates that shape directly via populate_by_name,
    since that's exactly how the ORM object's raw column value gets
    passed into CoverageReportOut in app/api/v1/coverage.py."""
    gap = AggregateGapOut.model_validate({
        "requirement_text_cluster": "Docker", "recurrence_count": 2,
        "recurrence_ratio": 0.5, "affected_job_post_ids": ["jp1", "jp2"],
        "current_support_level_distribution": {"unsupported": 2},
    })
    assert gap.requirement_text_cluster == "Docker"
    assert gap.recurrence_count == 2
