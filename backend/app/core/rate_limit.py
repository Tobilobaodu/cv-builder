"""In-memory rate limiting for auth endpoints.

Per security plan §1: slow down brute-force attempts on login/register
without reintroducing user data into the token path (no PII in JWT,
no persistent rate-limit storage that could itself become a timing
side-channel).

Uses a simple sliding-window counter in process memory. This is
intentionally per-process (not shared across API instances) — the
purpose is to make automated brute-force attacks impractical, not to
be a perfect distributed rate limiter. A Redis-backed implementation
can be swapped in behind the same interface later.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Tuple, Set

import redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Configuration ────────────────────────────────────────────────────

MAX_ATTEMPTS_PER_WINDOW = settings.rate_limit_auth_requests
WINDOW_SECONDS = settings.rate_limit_auth_window
BLOCKLIST_CLEANUP_INTERVAL = 300  # clean stale entries every 5 min


# ── State ────────────────────────────────────────────────────────────

# key → list of attempt timestamps (epoch seconds)
_attempts: dict[str, list[float]] = defaultdict(list)
# permanently blocked IPs after repeated window violations
_blocked: dict[str, float] = {}  # ip → blocked_until epoch
_last_cleanup = time.time()


def _cleanup_stale_entries(now: float) -> None:
    """Periodic cleanup of expired entries to prevent memory growth."""
    global _last_cleanup
    if now - _last_cleanup < BLOCKLIST_CLEANUP_INTERVAL:
        return
    _last_cleanup = now

    window_start = now - WINDOW_SECONDS
    expired_keys: list[str] = []

    for key, timestamps in _attempts.items():
        # Filter out timestamps outside the window
        active = [t for t in timestamps if t > window_start]
        if active:
            _attempts[key] = active
        else:
            expired_keys.append(key)

    for key in expired_keys:
        del _attempts[key]

    # Clean up expired blocklist entries
    expired_blocked = [ip for ip, until in _blocked.items() if until <= now]
    for ip in expired_blocked:
        del _blocked[ip]


def check_rate_limit(key: str) -> bool:
    """Return True if the request is within limits, False if rate-limited.

    Args:
        key: A unique identifier for the client, typically the client IP.

    Returns:
        True if the request should proceed, False if it should be blocked.
    """
    now = time.time()
    _cleanup_stale_entries(now)

    # Check persistent blocklist (repeated violations)
    if key in _blocked and _blocked[key] > now:
        return False

    # Sliding window: count attempts in the last WINDOW_SECONDS
    window_start = now - WINDOW_SECONDS
    active = [t for t in _attempts[key] if t > window_start]

    if len(active) >= MAX_ATTEMPTS_PER_WINDOW:
        # Violation: block for 5 minutes
        _blocked[key] = now + 300
        _attempts[key] = []
        return False

    active.append(now)
    _attempts[key] = active
    return True


# ── Non-auth tiers (upload / generation / url_fetch) ───────────────────
#
# Per security plan §1 / Phase 2.4: the same brute-force-slowing rationale
# applies to expensive, queue-consuming endpoints (file upload, job-post
# URL fetch, match/generation creation) as to auth. Deliberately separate
# state from the auth tier above — and from each other, via a tier-
# prefixed bucket key — so exhausting one tier's budget for a client never
# affects another tier's counters for that same client.

_tier_attempts: dict[str, list[float]] = defaultdict(list)
_tier_blocked: dict[str, float] = {}
_tier_last_cleanup = time.time()

# Largest configured window across all tiers — used only to size the
# cleanup sweep conservatively so it never purges an entry that might
# still be inside some tier's active window.
_MAX_TIER_WINDOW_SECONDS = max(
    settings.rate_limit_upload_window,
    settings.rate_limit_generation_window,
    settings.rate_limit_url_fetch_window,
    settings.rate_limit_trial_session_window,
)


def _cleanup_stale_tier_entries(now: float) -> None:
    """Periodic cleanup for the non-auth tiers' state (mirrors the auth cleanup above)."""
    global _tier_last_cleanup
    if now - _tier_last_cleanup < BLOCKLIST_CLEANUP_INTERVAL:
        return
    _tier_last_cleanup = now

    window_start = now - _MAX_TIER_WINDOW_SECONDS
    expired_keys: list[str] = []

    for key, timestamps in _tier_attempts.items():
        active = [t for t in timestamps if t > window_start]
        if active:
            _tier_attempts[key] = active
        else:
            expired_keys.append(key)

    for key in expired_keys:
        del _tier_attempts[key]

    expired_blocked = [k for k, until in _tier_blocked.items() if until <= now]
    for k in expired_blocked:
        del _tier_blocked[k]


def check_tier_rate_limit(tier: str, key: str, max_attempts: int, window_seconds: int) -> bool:
    """Sliding-window rate limit for a non-auth tier.

    Same algorithm as check_rate_limit() above, but keyed by `{tier}:{key}`
    against separate state, so this never interacts with the auth limiter
    or with a different tier's budget for the same client.

    Args:
        tier: Tier name, e.g. "upload", "generation", "url_fetch" — used
            only to namespace the bucket key.
        key: Client identifier, from get_client_key().
        max_attempts: Requests allowed per window for this tier.
        window_seconds: Window length in seconds for this tier.

    Returns:
        True if the request should proceed, False if it should be blocked.
    """
    now = time.time()
    _cleanup_stale_tier_entries(now)

    bucket_key = f"{tier}:{key}"

    if bucket_key in _tier_blocked and _tier_blocked[bucket_key] > now:
        return False

    window_start = now - window_seconds
    active = [t for t in _tier_attempts[bucket_key] if t > window_start]

    if len(active) >= max_attempts:
        _tier_blocked[bucket_key] = now + 300
        _tier_attempts[bucket_key] = []
        return False

    active.append(now)
    _tier_attempts[bucket_key] = active
    return True


def check_upload_rate_limit(key: str) -> bool:
    """Rate limit for CV file uploads (POST /cvs and reprocess)."""
    return check_tier_rate_limit(
        "upload", key, settings.rate_limit_upload_requests, settings.rate_limit_upload_window,
    )


def check_generation_rate_limit(key: str) -> bool:
    """Rate limit for job-creating endpoints that aren't upload or URL fetch
    (job-post text submission/reprocess, match creation, cover-letter
    workflow start/regenerate)."""
    return check_tier_rate_limit(
        "generation", key, settings.rate_limit_generation_requests, settings.rate_limit_generation_window,
    )


def check_url_fetch_rate_limit(key: str) -> bool:
    """Rate limit for job-post URL submission (the SSRF-relevant fetch path)."""
    return check_tier_rate_limit(
        "url_fetch", key, settings.rate_limit_url_fetch_requests, settings.rate_limit_url_fetch_window,
    )


def check_trial_session_rate_limit(key: str) -> bool:
    """Rate limit for trial-session *creation* (POST /trial-sessions).

    This is the one unauthenticated way to mint a new identity that can
    then consume upload/generation/url_fetch budget on its own — deserves
    its own, tighter tier rather than sharing one of the above.
    """
    return check_tier_rate_limit(
        "trial_session", key, settings.rate_limit_trial_session_requests, settings.rate_limit_trial_session_window,
    )


# ── Per-identity daily LLM spend budget (jbs-solution-sheet.md C1) ────
#
# check_generation_rate_limit above caps *request count* per client IP,
# not cost — a user with large CVs and long job posts can cost several
# times a normal user's spend at the same request rate, since
# _CV_TEXT_MAX_CHARS/_JOB_POST_MAX_CHARS (resume_rewrite_prompts.py)
# explicitly permit large inputs. Rate limits protect the service; this
# protects the business — a trial abuser doesn't need to exceed the
# request rate to cost money, just upload consistently large input.
#
# Redis-backed (not the in-memory tiers above) because spend has to
# survive across API worker processes and Celery workers, both of which
# can record spend for the same identity within one day.

DAILY_BUDGET_USD = {
    # No paid/subscription tier exists in the schema yet (User has no
    # plan/tier column) — "user" covers every authenticated account today.
    # Revisit once a real paid tier exists; this is deliberately generous
    # for now so no real signed-in user is throttled by a placeholder.
    "trial": 0.15,
    "user": 1.00,
}
_DEFAULT_BUDGET_TIER = "trial"

_redis_client: "redis.Redis | None" = None


def _get_redis() -> "redis.Redis":
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _llm_spend_key(identity_key: str) -> str:
    return f"llm_spend:{identity_key}:{_utc_today_iso()}"


def _utc_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def check_llm_budget(identity_key: str, tier: str) -> bool:
    """Return True if identity_key is within its daily LLM spend budget.

    Checked before a call, incremented after via record_llm_spend — racy
    by one call, which is the right trade: a blocking pre-reservation
    would cost a round trip on every single generation request to prevent
    an overage of, at most, one call's worth of pennies.

    Fails open on a Redis outage (returns True), the same trade the
    in-memory tier limiters above make implicitly by being best-effort —
    a metering outage must not take down generation entirely.
    """
    budget = DAILY_BUDGET_USD.get(tier, DAILY_BUDGET_USD[_DEFAULT_BUDGET_TIER])
    try:
        spent = float(_get_redis().get(_llm_spend_key(identity_key)) or 0)
    except Exception as e:
        logger.warning("llm_budget_check_failed", error=str(e))
        return True
    return spent < budget


def record_llm_spend(identity_key: str, usd_amount: float) -> None:
    """Add usd_amount to identity_key's running total for today. Best-
    effort — a lost spend record under-counts by one call, which is a far
    smaller failure than a Redis outage blocking every generation."""
    if usd_amount <= 0:
        return
    try:
        client = _get_redis()
        key = _llm_spend_key(identity_key)
        client.incrbyfloat(key, usd_amount)
        # 2 days: generous past the daily key's relevance, just enough to
        # survive a slow midnight-boundary read without growing forever.
        client.expire(key, 172_800)
    except Exception as e:
        logger.warning("llm_spend_record_failed", error=str(e))


def get_client_key(request) -> str:
    """Extract a rate-limiting key from the incoming request.

    Model A -- trusted client identity (Phase 2, Task 2.1): the key is the
    TCP peer address (request.client.host) only. Forwarded headers such as
    X-Forwarded-For are intentionally IGNORED: without a verified
    trusted-proxy configuration that blocks direct API access and is the
    sole allowed source of forwarded headers, a caller-supplied header is
    attacker-controlled and would let a caller spoof the key and bypass
    the limiter.

    A verified-proxy path (Model B) may be added later ONLY behind an
    explicit opt-in that documents the network guarantee making the
    header trustworthy (per 10-security-plan.md section 6). Do not add a
    fallback that trusts arbitrary forwarded headers.

    Falls back to "unknown" if the peer address is unavailable.
    """
    client_addr = getattr(request, "client", None)
    client_host = getattr(client_addr, "host", None)
    return client_host or "unknown"