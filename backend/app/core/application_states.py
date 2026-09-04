"""Explicit Application state machine — mirrors job_states.py's pattern
(StrEnum + an allowed-transitions table + a validated apply helper)
rather than leaving applications.status as a free-text column mutated
inline, the mistake job_states.py's own docstring says ProcessingJob
started out with.

Status values are the ones the response-rate product ask actually needs
to distinguish (deferred-items-plan.md's D5): "applied" is the initial,
inescapable-without-a-signal state; everything else records a real
external response (or its absence). GHOSTED is reachable manually only
today — there's no automated "no reply after N days" transition yet,
since that needs a scheduled sweep this pass doesn't add; a user marking
an application ghosted themselves is a legitimate, immediate use case on
its own.
"""

from enum import StrEnum


class ApplicationStatus(StrEnum):
    APPLIED = "applied"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED = "ghosted"


ALLOWED_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.APPLIED: {
        ApplicationStatus.INTERVIEWING, ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.GHOSTED,
    },
    ApplicationStatus.INTERVIEWING: {
        ApplicationStatus.OFFER, ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN, ApplicationStatus.GHOSTED,
    },
    ApplicationStatus.OFFER: {
        ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN,
    },
    # A ghosted application can still resurface — a late reply is a real
    # thing employers do — so GHOSTED isn't terminal the way
    # ACCEPTED/REJECTED/WITHDRAWN are.
    ApplicationStatus.GHOSTED: {
        ApplicationStatus.INTERVIEWING, ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.ACCEPTED: set(),
    ApplicationStatus.REJECTED: set(),
    ApplicationStatus.WITHDRAWN: set(),
}

# response_rate (GET /applications/stats) counts an application as
# "responded to" once it has left the initial APPLIED state for any
# reason other than the applicant withdrawing it themselves — withdrawal
# is the applicant's own action, not a signal the employer ever replied.
RESPONDED_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.INTERVIEWING, ApplicationStatus.OFFER,
        ApplicationStatus.ACCEPTED, ApplicationStatus.REJECTED,
    }
)


def assert_transition(current: str, target: str) -> None:
    """Raise ValueError if `current` -> `target` isn't a legal Application
    status transition. Both args accept plain strings (the column type) so
    callers don't need to import ApplicationStatus just to check."""
    try:
        current_status = ApplicationStatus(current)
        target_status = ApplicationStatus(target)
    except ValueError as e:
        raise ValueError(f"Unknown Application status in transition check: {e}") from e
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(f"Invalid Application transition: {current_status} -> {target_status}")


def transition_application_status(application, target: str) -> None:
    """Validate and apply an Application status transition in place. Does
    not commit — the caller controls its own commit boundary, matching
    job_states.py's transition_job_status."""
    assert_transition(application.status, target)
    application.status = target
