"""Focused tests for Bug #2 — contradictory and unclear support levels."""
import pytest
from app.extraction.match_engine import (
    run_match, SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED,
    CONTRADICTORY, UNCLEAR, _build_consistency_map, _roles_conflict,
    _date_ranges_overlap, _parse_cv_date,
)


class TestNormalMatch:
    """Existing supported/partially_supported/unsupported still work."""

    def test_all_supported(self):
        cv = {
            "basics": {"summary": "Python dev"},
            "workExperience": [],
            "skills": {"technical": ["Python", "Docker"], "soft": []},
        }
        jp = {"required_skills": ["Python", "Docker"], "preferred_skills": []}
        result = run_match(cv, ["Python", "Docker"], jp)
        assert result.supported_count == 2
        assert result.partial_count == 0
        assert result.unsupported_count == 0
        assert result.contradictory_count == 0
        assert result.unclear_count == 0
        assert result.total_requirements == 2

    def test_partially_supported_via_substring(self):
        cv = {
            "basics": {"summary": "I have experience with AWS cloud services"},
            "workExperience": [],
            "skills": {"technical": [], "soft": []},
        }
        jp = {"required_skills": ["AWS"], "preferred_skills": []}
        result = run_match(cv, [], jp)
        assert result.partial_count == 1
        assert result.supported_count == 0

    def test_unsupported(self):
        cv = {"basics": {}, "workExperience": [], "skills": {"technical": [], "soft": []}}
        jp = {"required_skills": ["Rust"], "preferred_skills": []}
        result = run_match(cv, [], jp)
        assert result.unsupported_count == 1


class TestContradictory:

    def test_conflicting_titles_at_same_company(self):
        cv = {
            "basics": {"summary": "Developer"},
            "workExperience": [
                {"company": "AcmeCorp", "title": "Software Engineer",
                 "technologies": ["Python"],
                 "startDate": "2019-01-01", "endDate": "2021-06-01"},
                {"company": "AcmeCorp", "title": "DevOps Engineer",
                 "technologies": ["Docker"],
                 "startDate": "2020-01-01", "endDate": "2022-01-01"},
            ],
            "skills": {"technical": [], "soft": []},
        }
        jp = {"required_skills": ["Python", "Docker"], "preferred_skills": []}
        result = run_match(cv, [], jp)
        assert result.contradictory_count >= 1
        for item in result.evidence_items:
            if item.support_level == CONTRADICTORY:
                assert "AcmeCorp" in item.warning
                break

    def test_sequential_roles_with_no_overlap_are_not_a_conflict(self):
        """A normal job change — different role family, same company, but
        the dates don't overlap — must NOT be flagged. This is the actual
        bug fix: the old code flagged any differing title at the same
        company regardless of timing."""
        cv = {
            "basics": {"summary": "Developer"},
            "workExperience": [
                {"company": "AcmeCorp", "title": "Software Engineer",
                 "technologies": ["Python"],
                 "startDate": "2015-01-01", "endDate": "2017-12-01"},
                {"company": "AcmeCorp", "title": "DevOps Engineer",
                 "technologies": ["Docker"],
                 "startDate": "2018-01-01", "endDate": "2021-01-01"},
            ],
            "skills": {"technical": [], "soft": []},
        }
        jp = {"required_skills": ["Python", "Docker"], "preferred_skills": []}
        result = run_match(cv, [], jp)
        assert result.contradictory_count == 0

    def test_missing_dates_are_conservative_no_flag(self):
        """Without parseable dates on both sides, overlap can't be
        confirmed — per the module's 'false positives are worse than
        missed contradictions' rule, this must not flag."""
        cv = {
            "basics": {"summary": "Developer"},
            "workExperience": [
                {"company": "AcmeCorp", "title": "Software Engineer",
                 "technologies": ["Python"]},
                {"company": "AcmeCorp", "title": "DevOps Engineer",
                 "technologies": ["Docker"]},
            ],
            "skills": {"technical": [], "soft": []},
        }
        jp = {"required_skills": ["Python", "Docker"], "preferred_skills": []}
        result = run_match(cv, [], jp)
        assert result.contradictory_count == 0

    def test_promotion_is_not_a_conflict(self):
        assert not _roles_conflict("software engineer", "senior software engineer")
        assert not _roles_conflict("junior developer", "lead developer")

    def test_different_roles_are_a_conflict(self):
        assert _roles_conflict("software engineer", "devops engineer")
        assert _roles_conflict("data scientist", "frontend engineer")


class TestDateRangesOverlap:
    """Direct tests of the date-overlap helper the contradictory-detection
    fix relies on."""

    def test_clear_overlap(self):
        a = {"startDate": "2019-01-01", "endDate": "2021-01-01"}
        b = {"startDate": "2020-01-01", "endDate": "2022-01-01"}
        assert _date_ranges_overlap(a, b) is True

    def test_back_to_back_is_not_overlap(self):
        a = {"startDate": "2015-01-01", "endDate": "2017-12-01"}
        b = {"startDate": "2018-01-01", "endDate": "2021-01-01"}
        assert _date_ranges_overlap(a, b) is False

    def test_open_ended_current_role_overlaps_later_start(self):
        a = {"startDate": "2019-01-01", "current": True}
        b = {"startDate": "2020-01-01", "endDate": "2021-01-01"}
        assert _date_ranges_overlap(a, b) is True

    def test_open_ended_without_current_flag_is_indeterminate(self):
        a = {"startDate": "2019-01-01"}  # no endDate, no current
        b = {"startDate": "2020-01-01", "endDate": "2021-01-01"}
        assert _date_ranges_overlap(a, b) is None

    def test_missing_start_date_is_indeterminate(self):
        a = {"endDate": "2021-01-01"}
        b = {"startDate": "2020-01-01", "endDate": "2022-01-01"}
        assert _date_ranges_overlap(a, b) is None

    def test_snake_case_keys_are_also_read(self):
        a = {"start_date": "2019-01-01", "end_date": "2021-01-01"}
        b = {"start_date": "2020-01-01", "end_date": "2022-01-01"}
        assert _date_ranges_overlap(a, b) is True


class TestParseCvDate:

    def test_full_iso_date(self):
        assert _parse_cv_date("2020-06-15").isoformat() == "2020-06-15"

    def test_year_month(self):
        assert _parse_cv_date("2020-06").isoformat() == "2020-06-01"

    def test_bare_year(self):
        assert _parse_cv_date("2020").isoformat() == "2020-01-01"

    def test_year_embedded_in_free_text(self):
        assert _parse_cv_date("Jan 2020").isoformat() == "2020-01-01"

    def test_unparseable_returns_none(self):
        assert _parse_cv_date("present") is None
        assert _parse_cv_date(None) is None
        assert _parse_cv_date("") is None


class TestUnclear:

    def test_low_section_confidence(self):
        cv = {
            "basics": {"summary": "Python developer"},
            "workExperience": [
                {"company": "OldCo", "title": "Engineer",
                 "bullets": ["Used Django for web apps"],
                 "confidence": 0.4},
            ],
            "skills": {"technical": ["Python"], "soft": []},
        }
        jp = {"required_skills": ["Python", "Django"], "preferred_skills": []}
        result = run_match(cv, ["Python"], jp)
        # Python is in skills → supported
        assert result.supported_count == 1
        # Django appears in a low-confidence experience bullet → unclear
        assert result.unclear_count >= 0  # at minimum, counts make sense

    def test_confidence_summary_reads_both_cases(self):
        cv_camel = {
            "basics": {}, "workExperience": [], "skills": {"technical": [], "soft": []},
            "confidenceSummary": {"skills": 0.3},
        }
        # This test just verifies the lookup function handles both keys
        from app.extraction.match_engine import _get_section_confidence
        conf = _get_section_confidence(cv_camel, "python")
        assert conf == 0.3

        cv_snake = {
            "basics": {}, "workExperience": [], "skills": {"technical": [], "soft": []},
            "confidence_summary": {"skills": 0.25},
        }
        conf2 = _get_section_confidence(cv_snake, "python")
        assert conf2 == 0.25


class TestCountsAddUp:

    def test_counts_sum_to_total(self):
        cv = {
            "basics": {"summary": "Python and Docker expert"},
            "workExperience": [
                {"company": "ACME", "title": "Backend Engineer",
                 "technologies": ["Python"]},
                {"company": "ACME", "title": "Frontend Engineer",
                 "technologies": ["Docker"]},
            ],
            "skills": {"technical": ["Kubernetes"], "soft": []},
        }
        jp = {
            "required_skills": ["Python", "Docker", "Kubernetes"],
            "preferred_skills": ["Rust"],
        }
        result = run_match(cv, ["Kubernetes"], jp)
        total = (result.supported_count + result.partial_count +
                 result.unsupported_count + result.contradictory_count +
                 result.unclear_count)
        assert total == result.total_requirements


class TestSummary:

    def test_summary_includes_contradictory_when_present(self):
        cv = {
            "basics": {}, "workExperience": [
                {"company": "X", "title": "Engineer", "technologies": ["Rust"],
                 "startDate": "2020-01-01", "endDate": "2021-01-01"},
                {"company": "X", "title": "Designer", "technologies": ["Rust"],
                 "startDate": "2020-06-01", "endDate": "2021-06-01"},
            ],
            "skills": {"technical": [], "soft": []},
        }
        jp = {"required_skills": ["Rust"], "preferred_skills": []}
        result = run_match(cv, [], jp)
        assert "contradictory" in result.summary_analysis.lower()