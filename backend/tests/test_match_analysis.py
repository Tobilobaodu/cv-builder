"""Tests for match_analysis.py against a fake LLM client — no real API
calls anywhere in this file. Mirrors test_cover_letter_generation.py's
FakeCompletions/FakeClient pattern exactly.

The key thing under test: the six MatchAnalysisResult count fields are
always derived in code from evidence_items, never taken from whatever the
model self-reports — see run_match_llm's own docstring.
"""
import json
from types import SimpleNamespace

import pytest

from app.services.match_analysis import MatchAnalysisError, run_match_llm


def _evidence(requirement_text, support_level, requirement_type="required", confidence=0.8):
    return {
        "requirementText": requirement_text,
        "requirementType": requirement_type,
        "supportLevel": support_level,
        "confidence": confidence,
        "sourceReferences": [],
        "suggestion": None,
        "warning": None,
    }


def _match_payload(evidence_items, **overrides):
    payload = {
        "score": 0.5,
        "summaryAnalysis": "Reasonable overlap with some gaps.",
        "evidenceItems": evidence_items,
        "atsIssues": [
            {"passed": True, "severity": "low", "title": "Contact details", "detail": "Present."},
        ],
        "formattingIssues": [],
        "tips": ["Quantify your Python achievements."],
    }
    payload.update(overrides)
    return payload


class FakeCompletions:
    def __init__(self, payload, refusal=None, prompt_tokens=150, completion_tokens=300):
        self.calls = []
        self._payload = payload
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
    def __init__(self, payload=None, **kwargs):
        payload = payload if payload is not None else _match_payload([])
        self.chat = SimpleNamespace(completions=FakeCompletions(payload, **kwargs))


class TestRunMatchLlm:
    def test_maps_schema_shaped_output_into_the_result_dataclass(self):
        items = [
            _evidence("Python", "supported"),
            _evidence("Kubernetes", "unsupported"),
        ]
        client = FakeClient(payload=_match_payload(items))

        result = run_match_llm("CV text.", "Job post text.", client=client)

        assert result.summary_analysis == "Reasonable overlap with some gaps."
        assert len(result.evidence_items) == 2
        assert result.evidence_items[0].requirement_text == "Python"
        assert result.evidence_items[0].support_level == "supported"
        assert result.ats_issues[0]["title"] == "Contact details"
        assert result.tips == ["Quantify your Python achievements."]
        assert result.prompt_tokens == 150
        assert result.completion_tokens == 300

    def test_counts_are_derived_from_evidence_items_not_self_reported(self):
        """Feed 2 supported + 1 unsupported evidence items alongside a
        deliberately bogus top-level score, and assert the derived counts
        match the evidence list regardless of what the model claims
        elsewhere in a hypothetical malformed response."""
        items = [
            _evidence("Python", "supported"),
            _evidence("SQL", "supported"),
            _evidence("Kubernetes", "unsupported"),
        ]
        client = FakeClient(payload=_match_payload(items, score=0.99))

        result = run_match_llm("CV text.", "Job post text.", client=client)

        assert result.supported_count == 2
        assert result.unsupported_count == 1
        assert result.partial_count == 0
        assert result.contradictory_count == 0
        assert result.unclear_count == 0
        assert result.total_requirements == 3
        # The top-level score is still taken (clamped), independent of counts.
        assert result.score == 0.99

    def test_all_five_support_levels_are_counted_independently(self):
        items = [
            _evidence("A", "supported"),
            _evidence("B", "partially_supported"),
            _evidence("C", "unsupported"),
            _evidence("D", "contradictory"),
            _evidence("E", "unclear"),
        ]
        client = FakeClient(payload=_match_payload(items))

        result = run_match_llm("CV text.", "Job post text.", client=client)

        assert result.supported_count == 1
        assert result.partial_count == 1
        assert result.unsupported_count == 1
        assert result.contradictory_count == 1
        assert result.unclear_count == 1
        assert result.total_requirements == 5

    def test_unknown_support_level_is_dropped_not_miscounted(self):
        items = [
            _evidence("A", "supported"),
            _evidence("B", "not_a_real_level"),
        ]
        client = FakeClient(payload=_match_payload(items))

        result = run_match_llm("CV text.", "Job post text.", client=client)

        assert result.total_requirements == 1
        assert result.supported_count == 1

    def test_empty_cv_text_raises_without_calling_the_client(self):
        client = FakeClient()
        with pytest.raises(MatchAnalysisError):
            run_match_llm("   ", "Job post text.", client=client)
        assert client.chat.completions.calls == []

    def test_empty_job_post_text_raises_without_calling_the_client(self):
        client = FakeClient()
        with pytest.raises(MatchAnalysisError):
            run_match_llm("CV text.", "", client=client)
        assert client.chat.completions.calls == []

    def test_model_refusal_raises_match_analysis_error(self):
        client = FakeClient(refusal="cannot help with this")
        with pytest.raises(MatchAnalysisError):
            run_match_llm("CV text.", "Job post text.", client=client)
