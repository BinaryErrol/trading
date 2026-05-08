"""Async SQLAlchemy engine, session factory, and connection pooling.

Provides the database infrastructure for the trading bot persistence layer.
Uses SQLAlchemy 2.0 async patterns with asyncpg as the PostgreSQL driver.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.persistence.models import Base

logger = structlog.get_logger(__name__)

# Module-level engine reference (set via init_db)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None

# Type alias for external use
AsyncSessionFactory = async_sessionmaker[AsyncSession]


def get_engine(
    url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
    echo: bool = False,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine with connection pooling.

    Args:
        url: Database connection URL (e.g., postgresql+asyncpg://user:pass@host/db).
        pool_size: Number of connections to maintain in the pool.
        max_overflow: Maximum overflow connections beyond pool_size.
        echo: If True, log all SQL statements.

    Returns:
        Configured AsyncEngine instance.
    """
    engine = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    logger.info(
        "database_engine_created",
        url=url.split("@")[-1] if "@" in url else url,  # Hide credentials
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    return engine


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The AsyncEngine to bind sessions to.

    Returns:
        An async_sessionmaker that produces AsyncSession instances.
    """
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def init_db(
    url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
    echo: bool = False,
    create_tables: bool = False,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Initialize the database engine and session factory.

    Sets the module-level engine and session factory for use by get_session().

    Args:
        url: Database connection URL.
        pool_size: Connection pool size.
        max_overflow: Max overflow connections.
        echo: Log SQL statements.
        create_tables: If True, create all tables (useful for testing).

    Returns:
        Tuple of (engine, session_factory).
    """
    global _engine, _session_factory

    _engine = get_engine(url, pool_size, max_overflow, echo)
    _session_factory = get_session_factory(_engine)

    if create_tables:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("database_tables_created")

    return _engine, _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session from the module-level factory.

    Usage:
        async with get_session() as session:
            result = await session.execute(select(PositionRecord))

    Yields:
        An AsyncSession that auto-commits on success and rolls back on error.

    Raises:
        RuntimeError: If init_db() has not been called.
    """
    if _session_factory is None:
        raise RuntimeError(
            "Database not initialized. Call init_db() before using get_session()."
        )

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """Close the database engine and release all connections."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        logger.info("database_engine_closed")
        _engine = None
        _session_factory = None
