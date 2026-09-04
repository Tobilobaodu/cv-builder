"""ATS readiness check tests — one per check, plus calibration / severity-enumeration.

Pure-function tests: no DB, no async, no Celery — only the scoring module.
"""
import pytest
from app.extraction.ats_check import (
    SEVERITY_DEDUCTIONS, CONTACT_INFO_UNPARSEABLE_CAP,
    TEXT_IN_IMAGE, LAYOUT_STRUCTURE, CONTACT_INFO_LOCATION,
    NON_STANDARD_CHARACTERS, SECTION_HEADING_RECOGNIZABILITY,
    FILE_FORMAT_SIGNALS,
    check_text_in_image, check_layout_structure, check_contact_info_location,
    check_non_standard_characters, check_section_heading_recognizability,
    check_file_format_signals, run_ats_check,
)

_CLEAN_CANONICAL = (
    "jane.doe@email.com  +1 555 0100\n"
    "WORK EXPERIENCE\nSoftware Engineer at Acme Corp\n - Built APIs in Python\n"
    "EDUCATION\nBSc Computer Science, University of Example\n"
    "SKILLS\nPython, SQL, Docker\n"
)

_CLEAN_PAYLOAD = {
    "basics": {"name": "Jane Doe", "email": "jane.doe@email.com", "phone": "+1 555 0100"},
    "workExperience": [], "education": [],
    "skills": {"technical": ["Python"], "soft": []},
}

_CLEAN_SV = {
    "sectionCountMatch": True, "headingAlignmentScore": 0.9,
    "readingOrderConsistent": True, "dateRangeConsistent": True,
    "bulletPreservationScore": 0.8, "anomalyDetected": False,
    "anomalyDetail": None,
}


class TestTextInImage:
    def test_clean_pass(self):
        result = check_text_in_image(
            docling_text="experienced python developer",
            textract_text="experienced python developer",
            ocr_used=False)
        assert result["passed"] is True
        assert result["check_type"] == TEXT_IN_IMAGE

    def test_image_embedded_flagged(self):
        result = check_text_in_image(
            docling_text="python and docker",
            textract_text="python docker kubernetes aws lambda",
            ocr_used=False)
        assert result["passed"] is False
        assert result["severity"] == "high"
        assert "image-embedded" in result["detail"].lower()

    def test_scanned_original_passes(self):
        result = check_text_in_image(
            docling_text="some text",
            textract_text="some text and way more from scan",
            ocr_used=True)
        assert result["passed"] is True


class TestLayoutStructure:
    def test_clean_pass(self):
        result = check_layout_structure(structural_validation=_CLEAN_SV)
        assert result["passed"] is True

    def test_absent_sv_not_assessed(self):
        result = check_layout_structure(structural_validation=None)
        assert result["passed"] is True
        assert "not assessed" in result["detail"].lower()

    def test_severe_line_ratio_grades_high(self):
        sv = dict(_CLEAN_SV, readingOrderConsistent=False)
        result = check_layout_structure(
            structural_validation=sv,
            docling_text="1\n2\n3\n",
            textract_text="1\n2\n3\n4\n5\n6\n7\n")
        assert result["passed"] is False
        assert result["severity"] == "high"

    def test_mild_line_ratio_grades_medium(self):
        sv = dict(_CLEAN_SV, readingOrderConsistent=False)
        result = check_layout_structure(
            structural_validation=sv,
            docling_text="a\nb\nc\nd\ne\n",
            textract_text="a\nb\nc\nd\ne\nf\ng\n")
        assert result["passed"] is False
        assert result["severity"] == "medium"


class TestContactInfoLocation:
    def test_clean_pass(self):
        check, parseable = check_contact_info_location(
            structured_payload=_CLEAN_PAYLOAD,
            canonical_text=_CLEAN_CANONICAL)
        assert check["passed"] is True
        assert parseable is True

    def test_missing_email_and_phone(self):
        payload = {"basics": {"name": "Jane", "email": "", "phone": ""}}
        check, parseable = check_contact_info_location(
            structured_payload=payload, canonical_text="some text")
        assert check["passed"] is False
        assert check["severity"] == "high"
        assert parseable is False

    def test_buried_contact_info(self):
        payload = {"basics": {"name": "J", "email": "j@x.com", "phone": "555"}}
        text = "EXPERIENCE\n" * 80 + "j@x.com 555"
        check, parseable = check_contact_info_location(
            structured_payload=payload, canonical_text=text)
        assert check["passed"] is False
        assert check["severity"] == "high"
        assert parseable is True


class TestNonStandardCharacters:
    def test_clean_pass(self):
        result = check_non_standard_characters(canonical_text="Just ASCII.")
        assert result["passed"] is True

    def test_private_use_area_flagged(self):
        result = check_non_standard_characters(
            canonical_text="Normal text \ue000 private use char.")
        assert result["passed"] is False
        assert result["severity"] == "low"
        assert "U+E000" in result["detail"]


class TestSectionHeadingRecognizability:
    def test_null_payload_not_assessed(self):
        result = check_section_heading_recognizability(structured_payload=None)
        assert result["passed"] is True
        assert "not assessed" in result["detail"].lower()

    def test_known_headings_pass(self):
        payload = {"heading_names": ["Work Experience", "Education", "Skills"]}
        result = check_section_heading_recognizability(structured_payload=payload)
        assert result["passed"] is True

    def test_unknown_heading_flagged(self):
        payload = {"heading_names": ["Volunteer Experience"]}
        result = check_section_heading_recognizability(structured_payload=payload)
        assert result["passed"] is False
        assert result["severity"] == "medium"

    def test_derived_from_raw_heading_flagged(self):
        payload = {"workExperience": [
            {"raw_heading": "Voluntary Experience", "title": "Volunteer"}]}
        result = check_section_heading_recognizability(structured_payload=payload)
        assert result["passed"] is False
        assert result["severity"] == "medium"


class TestFileFormatSignals:
    def test_clean_pass(self):
        result = check_file_format_signals(mime_type="application/pdf")
        assert result["passed"] is True

    def test_problematic_producer_flagged(self):
        result = check_file_format_signals(
            mime_type="application/pdf",
            merge_strategy_metadata={"pdf_producer": "QuarkXPress 2024"})
        assert result["passed"] is False
        assert result["severity"] == "low"
        assert "QuarkXPress" in result["detail"]


class TestOverallScore:
    def test_clean_cv_scores_perfect(self):
        result = run_ats_check(
            canonical_text=_CLEAN_CANONICAL,
            docling_text="experienced python developer",
            textract_text="experienced python developer",
            structural_validation=_CLEAN_SV,
            structured_payload=_CLEAN_PAYLOAD,
            mime_type="application/pdf")
        assert result.overall_score == 1.0
        assert result.contact_info_parseable is True
        assert all(c["passed"] for c in result.checks)

    def test_single_high_failure_drops_to_075(self):
        result = run_ats_check(
            canonical_text=_CLEAN_CANONICAL,
            docling_text="just python",
            textract_text="python kubernetes aws lambda docker",
            ocr_used=False,
            structural_validation=_CLEAN_SV,
            structured_payload=_CLEAN_PAYLOAD,
            mime_type="application/pdf")
        assert result.overall_score == 0.75

    def test_contact_info_unparseable_caps_at_04(self):
        payload = {"basics": {"name": "J", "email": "", "phone": ""}}
        result = run_ats_check(
            canonical_text=_CLEAN_CANONICAL,
            docling_text="python dev", textract_text="python dev",
            structural_validation=_CLEAN_SV,
            structured_payload=payload, mime_type="application/pdf")
        assert result.contact_info_parseable is False
        assert result.overall_score == 0.40

    def test_multiple_failures_additive_then_capped(self):
        sv = dict(_CLEAN_SV, readingOrderConsistent=False)
        payload = {"basics": {"name": "J", "email": "", "phone": ""}}
        result = run_ats_check(
            canonical_text="EXP\n" * 40 + "\U0001F000 dingbat",
            docling_text="just python",
            textract_text="python kubernetes aws lambda",
            ocr_used=False, structural_validation=sv,
            structured_payload=payload, mime_type="application/pdf")
        assert result.contact_info_parseable is False
        assert result.overall_score <= 0.40


class TestSeverityEnumeration:
    """Brute-force every per-check function across its pass/fail paths,
    collect all emitted severities, and assert they're all keys in
    SEVERITY_DEDUCTIONS — so a future added severity string is a caught
    test failure, not a runtime KeyError inside run_ats_check."""

    def test_all_check_types_covered(self):
        emitted: set[str] = set()

        emitted.add(check_text_in_image(
            docling_text="a", textract_text="a b", ocr_used=False)["severity"])
        emitted.add(check_text_in_image(
            docling_text="a b", textract_text="a b", ocr_used=False)["severity"])

        sv = {"readingOrderConsistent": False}
        emitted.add(check_layout_structure(
            structural_validation=sv,
            docling_text="1\n2\n3\n",
            textract_text="1\n2\n3\n4\n5\n6\n7\n")["severity"])
        emitted.add(check_layout_structure(
            structural_validation=sv,
            docling_text="a\nb\nc\nd\ne\n",
            textract_text="a\nb\nc\nd\ne\nf\ng\n")["severity"])
        emitted.add(check_layout_structure(
            structural_validation={"readingOrderConsistent": True})["severity"])

        payload = {"basics": {"email": "x@y.com", "phone": "555"}}
        text = "x@y.com 555\n" + "E\n" * 50
        emitted.add(check_contact_info_location(
            structured_payload=payload, canonical_text=text)[0]["severity"])
        emitted.add(check_contact_info_location(
            structured_payload={"basics": {"email": "", "phone": ""}},
            canonical_text="")[0]["severity"])

        emitted.add(check_non_standard_characters(
            canonical_text="\ue000")["severity"])
        emitted.add(check_non_standard_characters(
            canonical_text="clean")["severity"])

        emitted.add(check_section_heading_recognizability(
            structured_payload={"heading_names": ["Volunteer"]})["severity"])
        emitted.add(check_section_heading_recognizability(
            structured_payload={"heading_names": ["Work Experience"]})["severity"])

        emitted.add(check_file_format_signals(
            mime_type="application/pdf",
            merge_strategy_metadata={"pdf_producer": "QuarkXPress"})["severity"])
        emitted.add(check_file_format_signals(
            mime_type="application/pdf")["severity"])

        for sev in emitted:
            assert sev in SEVERITY_DEDUCTIONS, (
                f"severity '{sev}' emitted but missing from "
                f"SEVERITY_DEDUCTIONS keys {set(SEVERITY_DEDUCTIONS)}")

    def test_run_ats_check_does_not_keyerror(self):
        result = run_ats_check(
            canonical_text="\ue000",
            docling_text="a", textract_text="a b", ocr_used=False,
            structural_validation={"readingOrderConsistent": False},
            structured_payload={
                "basics": {"email": "", "phone": ""},
                "heading_names": ["Volunteer"]},
            mime_type="application/pdf",
            merge_strategy_metadata={"pdf_producer": "QuarkXPress"})
        assert isinstance(result.overall_score, float)


class TestConstants:
    def test_severity_deductions_are_positive(self):
        for sev, val in SEVERITY_DEDUCTIONS.items():
            assert val > 0, f"Deduction for {sev} must be positive"

    def test_cap_is_between_zero_and_one(self):
        assert 0 < CONTACT_INFO_UNPARSEABLE_CAP < 1

    def test_deductions_imported_not_hardcoded(self):
        from app.extraction import ats_check
        assert ats_check.SEVERITY_DEDUCTIONS is SEVERITY_DEDUCTIONS
        assert ats_check.CONTACT_INFO_UNPARSEABLE_CAP is CONTACT_INFO_UNPARSEABLE_CAP

        sv = dict(_CLEAN_SV, readingOrderConsistent=False)
        payload = {"basics": {"name": "J", "email": "", "phone": ""}}
        result = run_ats_check(
            canonical_text="EXP\n" * 40 + "\U0001F000 dingbat",
            docling_text="just python",
            textract_text="python kubernetes aws lambda",
            ocr_used=False, structural_validation=sv,
            structured_payload=payload, mime_type="application/pdf")
        assert result.contact_info_parseable is False
        # text_in_image high + layout high + nonstd low = 0.45; cap min(0.45,0.4)=0.4
        assert result.overall_score <= 0.40

        payload = {"heading_names": ["Volunteer Experience"]}
        result = check_section_heading_recognizability(structured_payload=payload)
        assert result["passed"] is False
        assert result["severity"] == "medium"

    def test_derived_from_raw_heading_flagged(self):
        payload = {"workExperience": [
            {"raw_heading": "Voluntary Experience", "title": "Volunteer"}]}
        result = check_section_heading_recognizability(structured_payload=payload)
        assert result["passed"] is False
        assert result["severity"] == "medium"
