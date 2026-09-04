"""Phase 2, Task 2.4: tiered rate limits for upload/generation/url_fetch.

Mirrors the limiter-level testing style of test_rate_limit_identity.py
(Task 2.1) — these tests exercise app.core.rate_limit directly rather than
spinning up endpoints, since that's where the actual sliding-window/
blocklist logic lives and what previously proved out the auth tier.
"""
import time

import pytest

import app.core.rate_limit as rl


@pytest.fixture(autouse=True)
def _reset_tier_limiter_state():
    """Reset tiered-limiter state before and after every test, independent
    of the auth limiter's state (which its own test file resets)."""
    rl._tier_attempts.clear()
    rl._tier_blocked.clear()
    rl._tier_last_cleanup = time.time()
    yield
    rl._tier_attempts.clear()
    rl._tier_blocked.clear()
    rl._tier_last_cleanup = time.time()


PEER = "203.0.113.50"


class TestTierIsolation:
    """Exhausting one tier's budget for a client must not affect another
    tier's budget for that same client, nor the auth limiter."""

    def test_upload_and_generation_tiers_independent_for_same_key(self):
        # Exhaust the upload tier for PEER.
        for _ in range(rl.settings.rate_limit_upload_requests):
            assert rl.check_upload_rate_limit(PEER)
        assert not rl.check_upload_rate_limit(PEER)

        # Generation tier for the same peer must be unaffected.
        assert rl.check_generation_rate_limit(PEER)

    def test_trial_session_tier_independent_from_others(self):
        for _ in range(rl.settings.rate_limit_trial_session_requests):
            assert rl.check_trial_session_rate_limit(PEER)
        assert not rl.check_trial_session_rate_limit(PEER)

        # Other tiers for the same peer must be unaffected.
        assert rl.check_upload_rate_limit(PEER)
        assert rl.check_generation_rate_limit(PEER)
        assert rl.check_url_fetch_rate_limit(PEER)

    def test_url_fetch_and_generation_tiers_independent_for_same_key(self):
        for _ in range(rl.settings.rate_limit_url_fetch_requests):
            assert rl.check_url_fetch_rate_limit(PEER)
        assert not rl.check_url_fetch_rate_limit(PEER)

        assert rl.check_generation_rate_limit(PEER)

    def test_tier_limiter_does_not_touch_auth_state(self):
        for _ in range(rl.settings.rate_limit_upload_requests):
            rl.check_upload_rate_limit(PEER)
        # Auth state (separate dicts) must be untouched.
        assert PEER not in rl._attempts
        assert PEER not in rl._blocked


class TestTierConfigWiring:
    """Each convenience wrapper must actually use its own tier's config
    values, not silently fall back to another tier's or auth's."""

    def test_upload_tier_uses_configured_limits(self):
        limit = rl.settings.rate_limit_upload_requests
        for _ in range(limit):
            assert rl.check_upload_rate_limit(PEER)
        assert not rl.check_upload_rate_limit(PEER)

    def test_generation_tier_uses_configured_limits(self):
        limit = rl.settings.rate_limit_generation_requests
        for _ in range(limit):
            assert rl.check_generation_rate_limit(PEER)
        assert not rl.check_generation_rate_limit(PEER)

    def test_url_fetch_tier_uses_configured_limits(self):
        limit = rl.settings.rate_limit_url_fetch_requests
        for _ in range(limit):
            assert rl.check_url_fetch_rate_limit(PEER)
        assert not rl.check_url_fetch_rate_limit(PEER)

    def test_trial_session_tier_uses_configured_limits(self):
        limit = rl.settings.rate_limit_trial_session_requests
        for _ in range(limit):
            assert rl.check_trial_session_rate_limit(PEER)
        assert not rl.check_trial_session_rate_limit(PEER)


class TestTwoPeerIndependencePerTier:
    """Same isolation guarantee the auth tier already has, extended to a
    non-auth tier — exhausting peer A leaves peer B unaffected."""

    def test_peer_b_unaffected_after_peer_a_exhausted(self):
        peer_a, peer_b = "198.51.100.9", "198.51.100.10"
        limit = rl.settings.rate_limit_generation_requests
        for _ in range(limit):
            assert rl.check_generation_rate_limit(peer_a)
        assert not rl.check_generation_rate_limit(peer_a)
        assert rl.check_generation_rate_limit(peer_b)


class TestGenericTierFunction:
    """check_tier_rate_limit() is the shared primitive the three
    convenience wrappers above are built on — test it directly with
    arbitrary tier names/limits, independent of settings."""

    def test_respects_max_attempts_and_window(self):
        key = "arbitrary-key"
        for _ in range(3):
            assert rl.check_tier_rate_limit("custom", key, max_attempts=3, window_seconds=60)
        assert not rl.check_tier_rate_limit("custom", key, max_attempts=3, window_seconds=60)

    def test_different_tier_names_for_same_key_are_independent(self):
        key = "same-client"
        for _ in range(2):
            assert rl.check_tier_rate_limit("tier-a", key, max_attempts=2, window_seconds=60)
        assert not rl.check_tier_rate_limit("tier-a", key, max_attempts=2, window_seconds=60)
        # A different tier name, same key, is a different bucket.
        assert rl.check_tier_rate_limit("tier-b", key, max_attempts=2, window_seconds=60)

    def test_block_persists_until_window_reopens_conceptually(self):
        # After a violation, the bucket is blocked outright (not just
        # windowed) — matches the auth tier's "block for 5 minutes" rule.
        key = "blocked-key"
        for _ in range(2):
            rl.check_tier_rate_limit("custom", key, max_attempts=2, window_seconds=60)
        assert not rl.check_tier_rate_limit("custom", key, max_attempts=2, window_seconds=60)
        # Immediately retrying is still blocked, even though we're not
        # over the raw window count anymore (the block, not the count,
        # governs).
        assert not rl.check_tier_rate_limit("custom", key, max_attempts=2, window_seconds=60)
