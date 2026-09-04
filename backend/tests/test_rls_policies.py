"""RLS policy tests — direct SQL-level proof that migration 018's policies
actually enforce, independent of any application-layer WHERE clause.

Deliberately queries as `app_runtime` (the non-superuser, non-owner role
migration 017 creates), not the owner role the rest of the suite runs
as — querying as the owner would always see every row regardless of
whether the policies are correct, since ENABLE-only RLS exempts the
owner by design (see 018's docstring). Skips (not fails) if app_runtime
hasn't been provisioned, so this file is safe to land before the
DATABASE_URL_RUNTIME_ASYNC cutover itself.

Matches the established live-DB test pattern (own NullPool engine, no
TestClient) used by test_idor_matrix.py.
"""
import sys
import types
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if "magic" not in sys.modules:
    _magic = types.ModuleType("magic")
    _magic.MagicException = Exception
    _magic.from_buffer = lambda *a, **k: "application/octet-stream"
    _magic.from_file = lambda *a, **k: "application/octet-stream"
    sys.modules["magic"] = _magic

from app.core.config import settings
from app.core.security import _rescope_after_commit, _set_rls_scope
from app.db.models import CvFile, User

_APP_RUNTIME_PASSWORD = "app_runtime_local"  # matches .env.example / migration 017's fallback


def _runtime_url() -> str:
    prefix, rest = settings.database_url_async.split("://", 1)
    _, host_part = rest.split("@", 1)
    return f"{prefix}://app_runtime:{_APP_RUNTIME_PASSWORD}@{host_part}"


_owner_engine = create_async_engine(settings.database_url_async, poolclass=NullPool)
_owner_session_factory = async_sessionmaker(_owner_engine, expire_on_commit=False)

_runtime_engine = create_async_engine(_runtime_url(), poolclass=NullPool)
_runtime_session_factory = async_sessionmaker(_runtime_engine, expire_on_commit=False)


async def _skip_if_no_app_runtime() -> None:
    async with _owner_session_factory() as s:
        result = await s.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'app_runtime'"))
        if result.scalar() is None:
            pytest.skip("app_runtime role not provisioned — run migration 017 first")


async def _seed_user() -> str:
    async with _owner_session_factory() as s:
        u = User(
            id=str(uuid.uuid4()), email=f"{uuid.uuid4().hex[:8]}@test.example",
            password_hash="fake", status="active",
        )
        s.add(u)
        await s.commit()
        return u.id


async def _seed_cv_file(owner_id: str) -> str:
    async with _owner_session_factory() as s:
        cv = CvFile(
            id=str(uuid.uuid4()), user_id=owner_id, filename="cv.pdf",
            mime_type="application/pdf", file_size=1, storage_key=str(uuid.uuid4()),
            status="parsed",
        )
        s.add(cv)
        await s.commit()
        return cv.id


@pytest.mark.asyncio(loop_scope="function")
async def test_no_guc_set_returns_zero_rows():
    """Negative control: app_runtime with no identity GUC sees nothing —
    proves enforcement is real, not just present."""
    await _skip_if_no_app_runtime()
    owner_id = await _seed_user()
    await _seed_cv_file(owner_id)

    async with _runtime_session_factory() as s:
        result = await s.execute(
            text("SELECT count(*) FROM cv_files WHERE user_id = :uid"), {"uid": owner_id}
        )
        assert result.scalar() == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_guc_set_to_owner_sees_only_own_row():
    """Positive test: identity A's GUC sees A's row, not B's — a SQL-level
    check, not a re-test of the application's own WHERE clause."""
    await _skip_if_no_app_runtime()
    owner_a = await _seed_user()
    owner_b = await _seed_user()
    cv_a = await _seed_cv_file(owner_a)
    await _seed_cv_file(owner_b)

    async with _runtime_session_factory() as s:
        await s.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": owner_a})
        result = await s.execute(
            text("SELECT id FROM cv_files WHERE user_id IN (:a, :b)"),
            {"a": owner_a, "b": owner_b},
        )
        assert [str(row[0]) for row in result.fetchall()] == [cv_a]


@pytest.mark.asyncio(loop_scope="function")
async def test_set_local_does_not_survive_a_commit():
    """Documents the exact gap get_scoped_session's commit-wrap
    (app/core/security.py) exists to close: SET LOCAL clears at
    transaction end, so a one-shot call stops applying the instant a
    mid-request commit happens."""
    await _skip_if_no_app_runtime()
    owner_id = await _seed_user()
    await _seed_cv_file(owner_id)

    async with _runtime_session_factory() as s:
        await s.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": owner_id})
        result = await s.execute(
            text("SELECT count(*) FROM cv_files WHERE user_id = :uid"), {"uid": owner_id}
        )
        assert result.scalar() == 1

        await s.commit()  # SQLAlchemy auto-begins a fresh transaction on the next statement

        result = await s.execute(
            text("SELECT count(*) FROM cv_files WHERE user_id = :uid"), {"uid": owner_id}
        )
        assert result.scalar() == 0


@pytest.mark.asyncio(loop_scope="function")
async def test_get_scoped_session_helpers_survive_a_commit():
    """The actual fix: the same _set_rls_scope/_rescope_after_commit pair
    get_scoped_session and get_scoped_session_for_user both use re-issues
    SET LOCAL after commit, so a second query in the same session still
    sees the identity's rows."""
    await _skip_if_no_app_runtime()
    owner_id = await _seed_user()
    await _seed_cv_file(owner_id)

    async with _runtime_session_factory() as s:
        await _set_rls_scope(s, owner_id)
        _rescope_after_commit(s, owner_id)

        await s.commit()

        result = await s.execute(
            text("SELECT count(*) FROM cv_files WHERE user_id = :uid"), {"uid": owner_id}
        )
        assert result.scalar() == 1
