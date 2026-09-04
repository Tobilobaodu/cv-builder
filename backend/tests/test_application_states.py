"""Unit tests for the Application status transition table — mirrors
test_job_states_classify_error.py's pattern for job_states.py."""

import pytest

from app.core.application_states import (
    ApplicationStatus,
    RESPONDED_STATUSES,
    assert_transition,
    transition_application_status,
)


def test_applied_can_move_to_interviewing():
    assert_transition("applied", "interviewing")  # no raise


def test_applied_can_move_to_rejected_directly():
    assert_transition("applied", "rejected")  # a same-day rejection, no interview stage


@pytest.mark.parametrize("target", ["accepted", "offer"])
def test_applied_cannot_skip_to_offer_or_accepted(target):
    with pytest.raises(ValueError):
        assert_transition("applied", target)


def test_offer_can_move_to_accepted():
    assert_transition("offer", "accepted")


@pytest.mark.parametrize("terminal", ["accepted", "rejected", "withdrawn"])
def test_terminal_statuses_allow_no_further_transition(terminal):
    with pytest.raises(ValueError):
        assert_transition(terminal, "applied")


def test_ghosted_can_recover_to_interviewing():
    """A late reply after being marked ghosted is a real thing — the
    state machine must not treat GHOSTED as terminal."""
    assert_transition("ghosted", "interviewing")


def test_unknown_status_raises():
    with pytest.raises(ValueError):
        assert_transition("applied", "not_a_real_status")


def test_transition_application_status_mutates_in_place():
    class FakeApplication:
        status = "applied"

    app = FakeApplication()
    transition_application_status(app, "interviewing")
    assert app.status == "interviewing"


def test_transition_application_status_rejects_invalid_transition():
    class FakeApplication:
        status = "rejected"

    app = FakeApplication()
    with pytest.raises(ValueError):
        transition_application_status(app, "interviewing")


def test_responded_statuses_excludes_applied_and_withdrawn():
    """withdrawn is the applicant's own action, not a signal the employer
    ever responded — it must not count toward the response rate."""
    assert ApplicationStatus.APPLIED not in RESPONDED_STATUSES
    assert ApplicationStatus.WITHDRAWN not in RESPONDED_STATUSES
    assert ApplicationStatus.REJECTED in RESPONDED_STATUSES
    assert ApplicationStatus.INTERVIEWING in RESPONDED_STATUSES
