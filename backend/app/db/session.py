"""SQLAlchemy async engine and session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings

engine = create_async_engine(
    settings.database_url_runtime_async or settings.database_url_async,
    echo=settings.environment == "local",
    pool_size=20,
    max_overflow=10,
    # pool_timeout: how long a request waits for a free connection before
    # raising, rather than hanging indefinitely under a connection-pool
    # saturation spike. connect_args timeout: asyncpg's own timeout for
    # establishing a new TCP connection, for the same reason.
    pool_timeout=30,
    connect_args={"timeout": 10},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    """FastAPI dependency: yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()