"""Five free-API job-feed source adapters (item 7).

Each `fetch_*` function calls one source's public API and returns a list
of normalized dicts (see `_NormalizedPosting` below) — never raises: a
single source being down/rate-limited/misconfigured must not stop the
other four from refreshing, so every adapter catches its own network and
parsing errors, logs them, and returns `[]`.

Only these 5: RemoteOK, Remotive, and Arbeitnow need no API key at all;
Reed and USAJobs each need a free registration (checked in config.py) and
degrade to a no-op with a logged reason when unconfigured, rather than
raising. LinkedIn/Indeed/Glassdoor are excluded entirely — none of the
three offers a free, ToS-compliant listings API; Adzuna is excluded too
pending its own separate 14-day evaluation, not implemented here.

One page per source per refresh (no pagination loop) — this task reruns
every `settings.job_feed_refresh_interval_seconds` (3h default), so the
first page of "latest" results is what actually matters; a full
historical backfill isn't the goal.
"""

from datetime import datetime, timezone
from typing import TypedDict

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.html_to_text import html_to_text

logger = get_logger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class NormalizedPosting(TypedDict):
    source: str
    external_id: str
    title: str
    company: str | None
    location: str | None
    remote: bool | None
    url: str
    description: str
    tags: list[str] | None
    salary_text: str | None
    posted_at: datetime | None


def _clean_description(raw: str | None) -> str:
    if not raw:
        return ""
    return html_to_text(raw) if raw.strip() else ""


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────
# RemoteOK — https://remoteok.com/api (no key)
# ──────────────────────────────────────────────────────────────────────


def fetch_remoteok() -> list[NormalizedPosting]:
    try:
        response = httpx.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "JBS-JobFeed/1.0"},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        raw_items = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("job_feed_fetch_failed", source="remoteok", error=str(e))
        return []

    postings: list[NormalizedPosting] = []
    for item in raw_items:
        # RemoteOK's first array element is a legal notice, not a listing.
        if not isinstance(item, dict) or "id" not in item or "position" not in item:
            continue
        salary_min, salary_max = item.get("salary_min"), item.get("salary_max")
        salary_text = f"${salary_min:,}–${salary_max:,}" if salary_min and salary_max else None
        postings.append(NormalizedPosting(
            source="remoteok",
            external_id=str(item["id"]),
            title=item.get("position") or item.get("slug") or "Untitled role",
            company=item.get("company"),
            location=item.get("location") or None,
            remote=True,
            url=item.get("url") or f"https://remoteok.com/remote-jobs/{item['id']}",
            description=_clean_description(item.get("description")),
            tags=item.get("tags") or None,
            salary_text=salary_text,
            posted_at=_parse_iso(item.get("date")),
        ))
    return postings


# ──────────────────────────────────────────────────────────────────────
# Remotive — https://remotive.com/api/remote-jobs (no key)
# ──────────────────────────────────────────────────────────────────────


def fetch_remotive() -> list[NormalizedPosting]:
    try:
        response = httpx.get(
            "https://remotive.com/api/remote-jobs",
            params={"limit": 100},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("job_feed_fetch_failed", source="remotive", error=str(e))
        return []

    postings: list[NormalizedPosting] = []
    for job in jobs:
        posted_at = None
        pub_date = job.get("publication_date")
        if pub_date:
            try:
                posted_at = datetime.strptime(pub_date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                posted_at = _parse_iso(pub_date)
        postings.append(NormalizedPosting(
            source="remotive",
            external_id=str(job["id"]),
            title=job.get("title") or "Untitled role",
            company=job.get("company_name"),
            location=job.get("candidate_required_location"),
            remote=True,
            url=job.get("url", ""),
            description=_clean_description(job.get("description")),
            tags=job.get("tags") or None,
            salary_text=job.get("salary") or None,
            posted_at=posted_at,
        ))
    return [p for p in postings if p["url"]]


# ──────────────────────────────────────────────────────────────────────
# Arbeitnow — https://www.arbeitnow.com/api/job-board-api (no key)
# ──────────────────────────────────────────────────────────────────────


def fetch_arbeitnow() -> list[NormalizedPosting]:
    try:
        response = httpx.get(
            "https://www.arbeitnow.com/api/job-board-api",
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json().get("data", [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("job_feed_fetch_failed", source="arbeitnow", error=str(e))
        return []

    postings: list[NormalizedPosting] = []
    for item in items:
        slug = item.get("slug")
        if not slug:
            continue
        posted_at = None
        created_at = item.get("created_at")
        if isinstance(created_at, (int, float)):
            posted_at = datetime.fromtimestamp(created_at, tz=timezone.utc)
        postings.append(NormalizedPosting(
            source="arbeitnow",
            external_id=str(slug),
            title=item.get("title") or "Untitled role",
            company=item.get("company_name"),
            location=item.get("location"),
            remote=item.get("remote"),
            url=item.get("url", ""),
            description=_clean_description(item.get("description")),
            tags=item.get("tags") or None,
            salary_text=None,
            posted_at=posted_at,
        ))
    return [p for p in postings if p["url"]]


# ──────────────────────────────────────────────────────────────────────
# Reed — https://www.reed.co.uk/api/1.0/search (free key required)
# ──────────────────────────────────────────────────────────────────────


def fetch_reed() -> list[NormalizedPosting]:
    if not settings.reed_api_key:
        logger.info("job_feed_source_skipped", source="reed", reason="REED_API_KEY not configured")
        return []

    try:
        response = httpx.get(
            "https://www.reed.co.uk/api/1.0/search",
            params={"resultsToTake": 100},
            auth=(settings.reed_api_key, ""),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("job_feed_fetch_failed", source="reed", error=str(e))
        return []

    postings: list[NormalizedPosting] = []
    for item in results:
        job_id = item.get("jobId")
        if job_id is None:
            continue
        min_sal, max_sal, currency = item.get("minimumSalary"), item.get("maximumSalary"), item.get("currency")
        salary_text = None
        if min_sal and max_sal:
            salary_text = f"{currency or ''} {min_sal:,.0f}–{max_sal:,.0f}".strip()
        posted_at = None
        date_str = item.get("date")
        if date_str:
            try:
                posted_at = datetime.strptime(date_str, "%d/%m/%Y").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        postings.append(NormalizedPosting(
            source="reed",
            external_id=str(job_id),
            title=item.get("jobTitle") or "Untitled role",
            company=item.get("employerName"),
            location=item.get("locationName"),
            remote=None,
            url=item.get("jobUrl", ""),
            description=_clean_description(item.get("jobDescription")),
            tags=None,
            salary_text=salary_text,
            posted_at=posted_at,
        ))
    return [p for p in postings if p["url"]]


# ──────────────────────────────────────────────────────────────────────
# USAJobs — https://data.usajobs.gov/api/search (free key + registered
# User-Agent email required)
# ──────────────────────────────────────────────────────────────────────


def fetch_usajobs() -> list[NormalizedPosting]:
    if not settings.usajobs_api_key or not settings.usajobs_user_agent_email:
        logger.info(
            "job_feed_source_skipped", source="usajobs",
            reason="USAJOBS_API_KEY / USAJOBS_USER_AGENT_EMAIL not configured",
        )
        return []

    try:
        response = httpx.get(
            "https://data.usajobs.gov/api/search",
            params={"ResultsPerPage": 100},
            headers={
                "Host": "data.usajobs.gov",
                "User-Agent": settings.usajobs_user_agent_email,
                "Authorization-Key": settings.usajobs_api_key,
            },
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json().get("SearchResult", {}).get("SearchResultItems", [])
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("job_feed_fetch_failed", source="usajobs", error=str(e))
        return []

    postings: list[NormalizedPosting] = []
    for item in items:
        descriptor = item.get("MatchedObjectDescriptor", {})
        external_id = item.get("MatchedObjectId") or descriptor.get("PositionID")
        if not external_id:
            continue
        remuneration = descriptor.get("PositionRemuneration") or []
        salary_text = None
        if remuneration:
            r = remuneration[0]
            min_range, max_range, interval = r.get("MinimumRange"), r.get("MaximumRange"), r.get("RateIntervalCode")
            if min_range and max_range:
                salary_text = f"${min_range}–${max_range} {interval or ''}".strip()
        description = (
            descriptor.get("UserArea", {}).get("Details", {}).get("JobSummary")
            or descriptor.get("QualificationSummary")
            or ""
        )
        postings.append(NormalizedPosting(
            source="usajobs",
            external_id=str(external_id),
            title=descriptor.get("PositionTitle") or "Untitled role",
            company=descriptor.get("OrganizationName"),
            location=descriptor.get("PositionLocationDisplay"),
            remote=None,
            url=descriptor.get("PositionURI", ""),
            description=_clean_description(description),
            tags=None,
            salary_text=salary_text,
            posted_at=_parse_iso(descriptor.get("PublicationStartDate")),
        ))
    return [p for p in postings if p["url"]]


ALL_ADAPTERS = {
    "remoteok": fetch_remoteok,
    "remotive": fetch_remotive,
    "arbeitnow": fetch_arbeitnow,
    "reed": fetch_reed,
    "usajobs": fetch_usajobs,
}
