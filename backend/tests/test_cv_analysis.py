"""Tests for cv_analysis.py against a fake LLM client — no real API calls
anywhere in this file. Mirrors test_cover_letter_generation.py's
FakeCompletions/FakeClient pattern exactly.
"""
import json
from types import SimpleNamespace

import pytest

from app.services.cv_analysis import CvAnalysisError, analyze_cv


def _analysis_payload(**overrides):
    payload = {
        "overallScore": 72.0,
        "skillsetScore": 65.0,
        "formattingScore": 80.0,
        "atsIssues": [
            {"passed": True, "severity": "low", "title": "Contact details", "detail": "Name, email, phone all present."},
            {"passed": False, "severity": "high", "title": "Section headings", "detail": "No clear 'Experience' heading found."},
        ],
        "formattingIssues": [
            {"passed": True, "severity": "low", "title": "Bullet points", "detail": "Experience uses bullet points."},
        ],
        "tips": ["Add a clear 'Experience' section heading.", "Quantify achievements where possible."],
        "basics": {"name": "Jane Doe", "email": "jane@example.com", "phone": None},
        "skills": ["Python", "SQL", "Kubernetes"],
    }
    payload.update(overrides)
    return payload


class FakeCompletions:
    def __init__(self, payload=None, refusal=None, prompt_tokens=100, completion_tokens=200):
        self.calls = []
        self._payload = payload if payload is not None else _analysis_payload()
        self._refusal = refusal
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._refusal:
            message = SimpleNamespace(content=None, refusal=self._refusal)
        else:
            message = SimpleNamespace(content=json.dumps(self._payload), refusal=None)
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=self._prompt_tokens, completion_tokens=self._completion_tokens)
        return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4o-mini-test")


class FakeClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=FakeCompletions(**kwargs))


class TestAnalyzeCv:
    def test_maps_schema_shaped_output_into_the_result_dataclass(self):
        client = FakeClient()
        result = analyze_cv("Jane Doe's CV text goes here.", client=client)

        assert result.overall_score == 72.0
        assert result.skillset_score == 65.0
        assert result.formatting_score == 80.0
        assert len(result.ats_issues) == 2
        assert result.ats_issues[1]["title"] == "Section headings"
        assert len(result.formatting_issues) == 1
        assert result.tips == [
            "Add a clear 'Experience' section heading.",
            "Quantify achievements where possible.",
        ]
        assert result.basics == {"name": "Jane Doe", "email": "jane@example.com", "phone": None}
        assert result.skills == ["Python", "SQL", "Kubernetes"]
        assert result.prompt_tokens == 100
        assert result.completion_tokens == 200

    def test_clamps_out_of_range_scores(self):
        client = FakeClient(payload=_analysis_payload(overallScore=150, skillsetScore=-10))
        result = analyze_cv("Some CV text.", client=client)
        assert result.overall_score == 100.0
        assert result.skillset_score == 0.0

    def test_empty_cv_text_raises_without_calling_the_client(self):
        client = FakeClient()
        with pytest.raises(CvAnalysisError):
            analyze_cv("   ", client=client)
        assert client.chat.completions.calls == []

    def test_model_refusal_raises_cv_analysis_error(self):
        client = FakeClient(refusal="cannot help with this")
        with pytest.raises(CvAnalysisError):
            analyze_cv("Some CV text.", client=client)

    def test_missing_optional_fields_default_to_empty(self):
        payload = _analysis_payload()
        payload["skills"] = []
        payload["tips"] = []
        client = FakeClient(payload=payload)
        result = analyze_cv("Some CV text.", client=client)
        assert result.skills == []
        assert result.tips == []
