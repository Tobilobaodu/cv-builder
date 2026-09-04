"""Focused tests for Bug #6 — auth schema alias alignment.

Validates that LoginResponse and UserResponse serialise with camelCase
keys matching 05-openapi.yaml, and accept camelCase input via
populate_by_name.
"""

from datetime import datetime, timezone
from app.schemas.auth import LoginResponse, UserResponse


def test_userresponse_camelcase_serialize():
    """UserResponse serializes with camelCase keys."""
    u = UserResponse(
        id="abc-123",
        email="test@example.com",
        account_status="active",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    d = u.model_dump(by_alias=True, mode="json")
    assert "accountStatus" in d, f"Expected accountStatus, got keys: {list(d)}"
    assert "account_status" not in d, "Should not leak snake_case"
    assert d["accountStatus"] == "active"
    assert d["createdAt"][:10] == "2026-01-01"


def test_userresponse_populate_by_name():
    """UserResponse accepts camelCase input (from client)."""
    u = UserResponse.model_validate({
        "id": "abc-123",
        "email": "test@example.com",
        "accountStatus": "active",
        "createdAt": "2026-01-01T00:00:00Z",
    })
    assert u.account_status == "active"
    assert u.created_at.year == 2026


def test_loginresponse_camelcase_serialize():
    """LoginResponse serializes with camelCase keys."""
    user = UserResponse(
        id="abc-123",
        email="test@example.com",
        account_status="active",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    lr = LoginResponse(
        access_token="tok-abc",
        refresh_token="ref-xyz",
        user=user,
    )
    d = lr.model_dump(by_alias=True, mode="json")
    assert "accessToken" in d, f"Expected accessToken, got keys: {list(d)}"
    assert "refreshToken" in d
    assert d["accessToken"] == "tok-abc"
    assert d["refreshToken"] == "ref-xyz"
    assert "access_token" not in d
    assert "refresh_token" not in d
    assert d["user"]["accountStatus"] == "active"


def test_loginresponse_populate_by_name():
    """LoginResponse accepts camelCase input (from client test)."""
    lr = LoginResponse.model_validate({
        "accessToken": "tok-abc",
        "refreshToken": "ref-xyz",
        "user": {
            "id": "u1",
            "email": "e@e.com",
            "accountStatus": "active",
            "createdAt": "2026-01-01T00:00:00Z",
        },
    })
    assert lr.access_token == "tok-abc"
    assert lr.refresh_token == "ref-xyz"
    assert lr.user.account_status == "active"