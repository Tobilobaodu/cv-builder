"""Live-DB test for refresh_all_sources's upsert/dedup behaviour — a
sync SQLAlchemy Session against the isolated test DB (conftest.py points
settings.database_url at it), with every adapter monkeypatched to return
canned data so this never touches real networks."""

import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import FeedJobPosting
from app.services.job_feed import ingest


def _canned_postings(source: str, n: int):
    return [
        {
            "source": source,
            "external_id": f"{source}-{i}",
            "title": f"Role {i}",
            "company": "Acme",
            "location": "Remote",
            "remote": True,
            "url": f"https://example.com/{source}/{i}",
            "description": "x" * 150,
            "tags": ["python"],
            "salary_text": None,
            "posted_at": None,
        }
        for i in range(n)
    ]


@pytest.fixture
def sync_session():
    engine = create_engine(settings.database_url)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_refresh_inserts_new_postings(monkeypatch, sync_session):
    tag = uuid.uuid4().hex[:8]
    monkeypatch.setitem(ingest.ALL_ADAPTERS, "remoteok", lambda: _canned_postings(f"remoteok{tag}", 3))
    for other in ("remotive", "arbeitnow", "reed", "usajobs"):
        monkeypatch.setitem(ingest.ALL_ADAPTERS, other, lambda: [])

    counts = ingest.refresh_all_sources(sync_session)
    sync_session.commit()

    assert counts["remoteok"] == 3


def test_refresh_is_idempotent_on_rerun(monkeypatch, sync_session):
    tag = uuid.uuid4().hex[:8]
    source_name = f"testsource{tag}"
    postings = _canned_postings(source_name, 5)

    monkeypatch.setattr(ingest, "ALL_ADAPTERS", {source_name: lambda: postings})

    first_counts = ingest.refresh_all_sources(sync_session)
    sync_session.commit()
    assert first_counts[source_name] == 5

    second_counts = ingest.refresh_all_sources(sync_session)
    sync_session.commit()
    assert second_counts[source_name] == 0, "re-running with the same (source, external_id) rows must insert nothing new"

    result = sync_session.execute(
        select(FeedJobPosting).where(FeedJobPosting.source == source_name)
    )
    assert len(result.scalars().all()) == 5


def test_refresh_one_source_failing_does_not_block_others(monkeypatch, sync_session):
    tag = uuid.uuid4().hex[:8]
    ok_source = f"oksource{tag}"

    def _broken():
        return []  # adapters never raise (see adapters.py) — a broken source just yields []

    monkeypatch.setattr(
        ingest, "ALL_ADAPTERS",
        {"brokensource": _broken, ok_source: lambda: _canned_postings(ok_source, 2)},
    )

    counts = ingest.refresh_all_sources(sync_session)
    sync_session.commit()

    assert counts["brokensource"] == 0
    assert counts[ok_source] == 2
