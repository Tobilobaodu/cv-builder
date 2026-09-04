"""Shared pytest configuration — isolates the test suite from the shared dev
database.

Before this file existed, every "live DB" test file built its own engine
directly against `settings.database_url_async` (`.env.local`'s value,
the same database `docker compose up` and manual testing use), with no
`conftest.py` and no teardown anywhere. Running the suite from the host venv
against the live Docker stack — the established local-testing pattern —
left every seeded row behind permanently: 2707 stray `@test.example` user
rows and 824 processing_jobs stuck at pending/queued had accumulated in the
dev DB by the time this was noticed.

This file does two things, both at true module level (before any test
module — which may build its own engine at *its* module level — gets
imported by pytest):

1. Points DATABASE_URL/DATABASE_URL_ASYNC/REDIS_URL at an isolated test
   database (`cv_tailoring_test`, created by
   backend/postgres-init/01-create-test-db.sql) and Redis db 1, instead of
   the shared dev DB / db 0. `os.environ.setdefault` so an explicit
   override (CI, a container run) still wins.
2. Runs `alembic upgrade head` against that database once per test session,
   then truncates every app table except `audit_events` (append-only by
   design — a DB trigger blocks UPDATE/DELETE but does not fire on
   TRUNCATE, so it's excluded explicitly rather than relying on the trigger)
   at the start of the run, so old runs' leftovers don't accumulate inside
   the isolated DB either.

Existing test files' own module-level `_test_engine =
create_async_engine(settings.database_url_async, ...)` calls need no
changes — they already read `settings`, which now resolves to the test DB
because this file's env-var overrides run first.
"""

import os

# Must happen before `app.core.config` (or anything importing it) is loaded
# anywhere in the process — Settings() is a module-level singleton built
# once at import time. pytest imports conftest.py before collecting test
# modules in the same/descendant directories, so this is safe here.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://cvapp:cvapp_local@localhost:5432/cv_tailoring_test",
)
os.environ.setdefault(
    "DATABASE_URL_ASYNC",
    "postgresql+asyncpg://cvapp:cvapp_local@localhost:5432/cv_tailoring_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from urllib.parse import urlparse

from app.core.config import settings
from app.db.models import Base


def _run_migrations() -> None:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "alembic")
    )
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")


def _assert_test_database(url: str) -> None:
    """Refuse to touch anything that doesn't look like an isolated test
    database. This fixture TRUNCATEs every app table — it wiped the shared
    dev database once already when a caller's env override pointed
    DATABASE_URL at `cv_tailoring` instead of `cv_tailoring_test`, and
    `os.environ.setdefault` above can't protect against that since an
    explicit override always wins. Fail loudly instead."""
    db_name = urlparse(url).path.lstrip("/")
    if not db_name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run the test suite against database {db_name!r} — "
            "expected a name ending in '_test'. This fixture TRUNCATEs "
            "every table; do not override DATABASE_URL/DATABASE_URL_ASYNC "
            "when running pytest — conftest.py already points them at the "
            "correct isolated database by default."
        )


@pytest.fixture(scope="session", autouse=True)
def _isolated_test_database():
    """Runs once per test session: migrate, then clear stale rows from any
    previous run. Excludes audit_events (append-only) and alembic_version
    (migration bookkeeping, not app data)."""
    _assert_test_database(settings.database_url_async)
    _run_migrations()

    async def _truncate_all():
        engine = create_async_engine(settings.database_url_async)
        try:
            async with engine.begin() as conn:
                table_names = [
                    t.name
                    for t in Base.metadata.sorted_tables
                    if t.name not in ("audit_events", "alembic_version")
                ]
                if table_names:
                    await conn.execute(
                        text(
                            f"TRUNCATE TABLE {', '.join(table_names)} "
                            "RESTART IDENTITY CASCADE"
                        )
                    )
        finally:
            await engine.dispose()

    import asyncio

    asyncio.run(_truncate_all())
    yield
