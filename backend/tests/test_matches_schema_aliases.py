"""matches.py's MatchResponse/EvidenceItemOut had the field/alias
inversion bug (camelCase field name, snake_case alias — the inverse of
this codebase's correct convention) already found and fixed in
cover_letter.py and job_posts.py earlier, but missed in matches.py at the
time. Found live via a real GET /matches/{matchId} call during the Phase 2
extraction extension's walkthrough: the response body was genuinely
serializing as snake_case (supported_count, total_requirements,
requirement_text, ...) despite 05-openapi.yaml documenting camelCase.
"""

from app.api.v1.matches import MatchResponse, EvidenceItemOut


def test_match_response_serializes_camelcase():
    m = MatchResponse(
        id="m1", status="completed", score=0.5,
        supported_count=1, partial_count=2, unsupported_count=3,
        total_requirements=6, summary_analysis="Matched 1 of 6.",
        evidence_items=None, error_message=None,
        created_at="2026-01-01T00:00:00Z", completed_at=None,
    )
    d = m.model_dump(by_alias=True, mode="json")
    assert "supportedCount" in d, f"Expected supportedCount, got keys: {list(d)}"
    assert "totalRequirements" in d
    assert "partialCount" in d
    assert "unsupportedCount" in d
    assert "summaryAnalysis" in d
    assert "supported_count" not in d, "Should not leak snake_case"
    assert "total_requirements" not in d, "Should not leak snake_case"
    assert d["supportedCount"] == 1
    assert d["totalRequirements"] == 6


def test_evidence_item_out_serializes_camelcase():
    e = EvidenceItemOut(
        id="e1", requirement_text="Python", requirement_type="required",
        support_level="supported", confidence=0.85,
        source_references=["skill:Python"],
    )
    d = e.model_dump(by_alias=True, mode="json")
    assert "requirementText" in d
    assert "requirementType" in d
    assert "supportLevel" in d
    assert "sourceReferences" in d
    assert "requirement_text" not in d, "Should not leak snake_case"
    assert d["requirementText"] == "Python"
    assert d["supportLevel"] == "supported"


def test_match_response_accepts_camelcase_input():
    m = MatchResponse.model_validate({
        "id": "m1", "status": "completed", "score": 0.5,
        "supportedCount": 1, "totalRequirements": 6,
        "createdAt": "2026-01-01T00:00:00Z",
    })
    assert m.supported_count == 1
    assert m.total_requirements == 6
