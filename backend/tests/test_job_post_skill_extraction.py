"""Tests for job_post_skill_extraction.py (M3) — against a fake LLM
client, no real API calls. Covers: verification against source text
(never fabricates), graceful failure (never blocks the parse), and the
should_enrich() cost-gating decision.
"""
import json
from types import SimpleNamespace

from app.core.config import settings
from app.services.job_post_skill_extraction import extract_skills_via_llm, should_enrich


class FakeCompletions:
    def __init__(self, skills, raise_error=None):
        self._skills = skills
        self._raise_error = raise_error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_error:
            raise self._raise_error
        content = json.dumps({"skills": self._skills})
        message = SimpleNamespace(content=content, refusal=None)
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(prompt_tokens=40, completion_tokens=15)
        return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4o-mini-2024-07-18")


class FakeClient:
    def __init__(self, skills=None, raise_error=None):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(skills or [], raise_error=raise_error)
        )


RAW_TEXT = (
    "We are looking for someone with strong stakeholder management "
    "experience and a background in usability testing across mobile "
    "and web products."
)


class TestExtractSkillsViaLlm:
    def test_grounded_skills_are_verified_and_returned(self):
        client = FakeClient(skills=["stakeholder management", "usability testing"])
        result = extract_skills_via_llm(RAW_TEXT, llm_client_override=client)
        assert "stakeholder management" in result
        assert "usability testing" in result

    def test_invented_skill_not_in_source_text_is_rejected(self):
        client = FakeClient(skills=["nuclear reactor operation"])
        result = extract_skills_via_llm(RAW_TEXT, llm_client_override=client)
        assert result == []

    def test_mix_of_grounded_and_invented_keeps_only_grounded(self):
        client = FakeClient(skills=["stakeholder management", "quantum cryptography"])
        result = extract_skills_via_llm(RAW_TEXT, llm_client_override=client)
        assert "stakeholder management" in result
        assert "quantum cryptography" not in result

    def test_empty_raw_text_returns_empty_without_calling_llm(self):
        client = FakeClient(skills=["stakeholder management"])
        result = extract_skills_via_llm("", llm_client_override=client)
        assert result == []
        assert client.chat.completions.calls == []

    def test_llm_call_failure_returns_empty_list_not_an_exception(self):
        from app.services.llm_client import LlmCallError
        client = FakeClient(raise_error=LlmCallError("boom"))
        result = extract_skills_via_llm(RAW_TEXT, llm_client_override=client)
        assert result == []

    def test_non_list_skills_field_returns_empty_list(self):
        client = FakeClient()
        client.chat.completions._skills = "not a list"
        result = extract_skills_via_llm(RAW_TEXT, llm_client_override=client)
        assert result == []


class TestShouldEnrich:
    def test_enriches_when_below_count_threshold(self):
        assert should_enrich([], []) is True
        assert should_enrich(["a"], ["b"]) is True

    def test_does_not_enrich_when_count_sufficient_and_terms_are_short(self):
        threshold = settings.job_post_llm_enrichment_min_requirements
        skills = [f"skill-{i}" for i in range(threshold)]
        assert should_enrich(skills, []) is False

    def test_enriches_when_count_sufficient_but_items_are_prose(self):
        """The real bug this test locks in: a healthy item *count* that's
        actually full sentences, not skill terms — confirmed against a
        real posting where count-only gating silently skipped the exact
        case this feature exists for."""
        qualifications = [
            "Around 4+ years of product design experience, with consumer products shipped",
            "Have a portfolio of shipped consumer work you can talk about honestly",
            "Strong visual and interaction craft that holds up under close inspection",
        ]
        assert should_enrich([], qualifications) is True

    def test_disabled_via_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "job_post_llm_enrichment_enabled", False)
        assert should_enrich([], []) is False
