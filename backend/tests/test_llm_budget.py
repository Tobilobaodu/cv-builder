"""Per-identity daily LLM spend budget (jbs-solution-sheet.md C1).

Uses the real (test-isolated, Redis db 1 per conftest.py) Redis instance
rather than mocking — the thing worth verifying is the actual Redis
read/increment/expire round trip, not that Python calls a mock correctly.
"""

import uuid

import pytest

from app.core.rate_limit import (
    DAILY_BUDGET_USD,
    check_llm_budget,
    record_llm_spend,
)


@pytest.fixture
def identity_key():
    # Unique per test so runs never see another test's leftover spend.
    return f"test-{uuid.uuid4().hex}"


def test_fresh_identity_is_within_budget(identity_key):
    assert check_llm_budget(identity_key, "trial") is True


def test_spend_below_budget_still_passes(identity_key):
    record_llm_spend(identity_key, DAILY_BUDGET_USD["trial"] / 2)
    assert check_llm_budget(identity_key, "trial") is True


def test_spend_at_or_over_budget_fails(identity_key):
    record_llm_spend(identity_key, DAILY_BUDGET_USD["trial"])
    assert check_llm_budget(identity_key, "trial") is False


def test_spend_accumulates_across_calls(identity_key):
    half = DAILY_BUDGET_USD["trial"] / 2
    record_llm_spend(identity_key, half)
    assert check_llm_budget(identity_key, "trial") is True
    record_llm_spend(identity_key, half + 0.001)
    assert check_llm_budget(identity_key, "trial") is False


def test_tiers_have_independent_budgets():
    # Same underlying spend, different tier ceilings — "user" (no paid
    # tier exists yet, see rate_limit.py's comment) is deliberately more
    # generous than "trial".
    assert DAILY_BUDGET_USD["user"] > DAILY_BUDGET_USD["trial"]


def test_zero_or_negative_spend_is_a_noop(identity_key):
    record_llm_spend(identity_key, 0)
    record_llm_spend(identity_key, -1)
    assert check_llm_budget(identity_key, "trial") is True


def test_unknown_tier_falls_back_to_trial_budget(identity_key):
    record_llm_spend(identity_key, DAILY_BUDGET_USD["trial"])
    assert check_llm_budget(identity_key, "some-future-tier") is False
