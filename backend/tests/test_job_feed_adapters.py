"""Unit tests for the 5 job-feed source adapters — mocks httpx.get so
these never hit real network in CI/local runs. Covers: normal-shape
parsing per source, RemoteOK's legal-notice-first-element skip, and the
Reed/USAJobs no-key-configured no-op."""

import httpx
import pytest

from app.services.job_feed import adapters


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_remoteok_skips_legal_notice_first_element(monkeypatch):
    payload = [
        {"legal": "https://remoteok.com/legal", "id": "legal-notice"},
        {
            "id": 123, "position": "Backend Engineer", "company": "Acme",
            "location": "Worldwide", "url": "https://remoteok.com/remote-jobs/123",
            "description": "<p>Build things</p>", "tags": ["python"],
            "date": "2026-01-01T00:00:00+00:00",
        },
    ]
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    postings = adapters.fetch_remoteok()
    assert len(postings) == 1
    assert postings[0]["external_id"] == "123"
    assert postings[0]["title"] == "Backend Engineer"
    assert postings[0]["remote"] is True
    assert "Build things" in postings[0]["description"]


def test_remotive_parses_jobs_array(monkeypatch):
    payload = {
        "jobs": [
            {
                "id": 456, "title": "Data Engineer", "company_name": "Beta",
                "candidate_required_location": "Anywhere", "url": "https://remotive.com/job/456",
                "description": "Great role", "tags": ["sql"],
                "publication_date": "2026-01-02 12:00:00", "salary": "$100k",
            }
        ]
    }
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    postings = adapters.fetch_remotive()
    assert len(postings) == 1
    assert postings[0]["external_id"] == "456"
    assert postings[0]["salary_text"] == "$100k"
    assert postings[0]["posted_at"] is not None


def test_arbeitnow_parses_data_array_and_unix_timestamp(monkeypatch):
    payload = {
        "data": [
            {
                "slug": "backend-engineer-acme", "title": "Backend Engineer", "company_name": "Acme",
                "location": "Berlin", "remote": True, "url": "https://arbeitnow.com/job/backend-engineer-acme",
                "description": "Join us", "tags": ["python"], "created_at": 1735689600,
            }
        ]
    }
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    postings = adapters.fetch_arbeitnow()
    assert len(postings) == 1
    assert postings[0]["external_id"] == "backend-engineer-acme"
    assert postings[0]["remote"] is True
    assert postings[0]["posted_at"] is not None


def test_arbeitnow_skips_items_without_slug(monkeypatch):
    payload = {"data": [{"title": "No slug here", "url": "https://x.com"}]}
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    assert adapters.fetch_arbeitnow() == []


def test_reed_skipped_when_no_api_key(monkeypatch):
    monkeypatch.setattr(adapters.settings, "reed_api_key", "")

    def _fail(*a, **k):
        raise AssertionError("should not call httpx.get when unconfigured")

    monkeypatch.setattr(adapters.httpx, "get", _fail)
    assert adapters.fetch_reed() == []


def test_reed_parses_results_when_key_configured(monkeypatch):
    monkeypatch.setattr(adapters.settings, "reed_api_key", "fake-key")
    payload = {
        "results": [
            {
                "jobId": 789, "jobTitle": "Backend Engineer", "employerName": "Gamma",
                "locationName": "London", "jobUrl": "https://reed.co.uk/jobs/789",
                "jobDescription": "Role summary", "date": "15/01/2026",
                "minimumSalary": 50000, "maximumSalary": 60000, "currency": "GBP",
            }
        ]
    }
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    postings = adapters.fetch_reed()
    assert len(postings) == 1
    assert postings[0]["external_id"] == "789"
    assert postings[0]["salary_text"] == "GBP 50,000–60,000"


def test_usajobs_skipped_when_unconfigured(monkeypatch):
    monkeypatch.setattr(adapters.settings, "usajobs_api_key", "")
    monkeypatch.setattr(adapters.settings, "usajobs_user_agent_email", "")

    def _fail(*a, **k):
        raise AssertionError("should not call httpx.get when unconfigured")

    monkeypatch.setattr(adapters.httpx, "get", _fail)
    assert adapters.fetch_usajobs() == []


def test_usajobs_parses_search_result_items(monkeypatch):
    monkeypatch.setattr(adapters.settings, "usajobs_api_key", "fake-key")
    monkeypatch.setattr(adapters.settings, "usajobs_user_agent_email", "test@example.com")
    payload = {
        "SearchResult": {
            "SearchResultItems": [
                {
                    "MatchedObjectId": "abc123",
                    "MatchedObjectDescriptor": {
                        "PositionTitle": "IT Specialist", "OrganizationName": "Dept of Example",
                        "PositionLocationDisplay": "Washington, DC",
                        "PositionURI": "https://usajobs.gov/job/abc123",
                        "PublicationStartDate": "2026-01-03",
                        "UserArea": {"Details": {"JobSummary": "Summary text"}},
                        "PositionRemuneration": [{"MinimumRange": "80000", "MaximumRange": "90000", "RateIntervalCode": "Per Year"}],
                    },
                }
            ]
        }
    }
    monkeypatch.setattr(adapters.httpx, "get", lambda *a, **k: _FakeResponse(payload))

    postings = adapters.fetch_usajobs()
    assert len(postings) == 1
    assert postings[0]["external_id"] == "abc123"
    assert postings[0]["title"] == "IT Specialist"
    assert "80000" in postings[0]["salary_text"]


def test_adapter_returns_empty_list_on_network_error(monkeypatch):
    def _raise(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(adapters.httpx, "get", _raise)
    assert adapters.fetch_remoteok() == []
    assert adapters.fetch_remotive() == []
    assert adapters.fetch_arbeitnow() == []
