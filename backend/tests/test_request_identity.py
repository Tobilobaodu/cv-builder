"""Sprint 2: DB-independent tests for RequestIdentity and identity_owner_filter.

These cover the pure logic — the dataclass and the filter-expression
builder. get_current_user_or_trial_session() itself queries the database
(TrialSession lookup, expiry/claim checks) and is exercised by
test_job_concurrency_limit.py-style integration tests against a real
Postgres connection instead — see the note in the roadmap doc about what
still needs a live DB to verify.
"""
import pytest

from app.core.security import RequestIdentity, identity_owner_filter
from app.db.models import CvFile, User


class TestRequestIdentity:

    def test_user_identity_exposes_user_id_only(self):
        user = User(id="u-1", email="a@example.com", password_hash="x")
        identity = RequestIdentity(user=user, trial_session=None)
        assert identity.user_id == "u-1"
        assert identity.trial_session_id is None

    def test_trial_identity_exposes_trial_session_id_only(self):
        from app.db.models import TrialSession
        from datetime import datetime, timezone
        ts = TrialSession(id="ts-1", expires_at=datetime.now(timezone.utc))
        identity = RequestIdentity(user=None, trial_session=ts)
        assert identity.trial_session_id == "ts-1"
        assert identity.user_id is None


class TestIdentityOwnerFilter:
    """The filter builder must pick the right column based on which side
    of the identity is populated — never both, matching the DB CHECK
    constraint's own invariant."""

    def test_user_identity_filters_on_user_id(self):
        user = User(id="u-1", email="a@example.com", password_hash="x")
        identity = RequestIdentity(user=user, trial_session=None)
        expr = identity_owner_filter(CvFile, identity)
        # SQLAlchemy BinaryExpression — check it's built against the right
        # column and bound to the right value without needing a DB.
        assert expr.left.name == "user_id"
        assert expr.right.value == "u-1"

    def test_trial_identity_filters_on_trial_session_id(self):
        from app.db.models import TrialSession
        from datetime import datetime, timezone
        ts = TrialSession(id="ts-1", expires_at=datetime.now(timezone.utc))
        identity = RequestIdentity(user=None, trial_session=ts)
        expr = identity_owner_filter(CvFile, identity)
        assert expr.left.name == "trial_session_id"
        assert expr.right.value == "ts-1"
