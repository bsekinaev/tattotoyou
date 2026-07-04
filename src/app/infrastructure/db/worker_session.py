"""Изолированные SQLAlchemy-сессии для синхронных Celery-задач.

Каждый вызов ``asyncio.run`` создаёт собственный event loop. Поэтому Celery не
должен переиспользовать глобальный async pool FastAPI между разными loop.
``NullPool`` создаёт соединение в текущем loop и закрывает его вместе с engine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@asynccontextmanager
async def worker_session_scope() -> AsyncIterator[AsyncSession]:
    """Создать loop-local engine/session и гарантированно освободить ресурсы."""
    settings = get_settings()
    engine = create_async_engine(
        settings.postgres_dsn,
        echo=settings.debug,
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.postgres_connect_timeout_seconds},
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
