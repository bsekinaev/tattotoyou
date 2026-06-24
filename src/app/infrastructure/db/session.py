"""
Настройка асинхронного подключения к PostgreSQL.
"""

from collections.abc import AsyncGenerator

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# ============================================
# СОЗДАНИЕ АСИНХРОННОГО ДВИЖКА (ENGINE)
# ============================================
async_engine = create_async_engine(
    settings.postgres_dsn,
    echo=settings.debug,  # В DEBUG режиме печатать SQL-запросы в консоль
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"connect_timeout": settings.postgres_connect_timeout_seconds},
)

# ============================================
# ФАБРИКА СЕССИЙ
# ============================================
async_session_factory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ============================================
# DEPENDENCY ДЛЯ FASTAPI
# ============================================
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency для FastAPI."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except HTTPException:
            raise
        except Exception:
            await session.rollback()
            logger.exception("database_session_error")
            raise
        finally:
            await session.close()


# ============================================
# ФУНКЦИИ ДЛЯ LIFESPAN (startup/shutdown)
# ============================================
async def init_db() -> None:
    """Инициализация БД при старте приложения."""
    logger.info("database_initializing")
    async with async_engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("database_initialized")


async def close_db() -> None:
    """Закрытие пула соединений при остановке приложения."""
    logger.info("database_closing")
    await async_engine.dispose()
    logger.info("database_closed")