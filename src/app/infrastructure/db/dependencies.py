"""
Dependency Injection для репозиториев.
FastAPI будет автоматически создавать репозитории с активной сессией БД.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.infrastructure.db.session import get_db_session
from app.infrastructure.db.repositories import (
    PlatformRepository,
    ClientRepository,
    ConversationRepository,
    MessageRepository,
)


def get_platform_repo(db: AsyncSession = Depends(get_db_session)) -> PlatformRepository:
    """Получить репозиторий платформ."""
    return PlatformRepository(db)


def get_client_repo(db: AsyncSession = Depends(get_db_session)) -> ClientRepository:
    """Получить репозиторий клиентов."""
    return ClientRepository(db)


def get_conversation_repo(db: AsyncSession = Depends(get_db_session)) -> ConversationRepository:
    """Получить репозиторий диалогов."""
    return ConversationRepository(db)


def get_message_repo(db: AsyncSession = Depends(get_db_session)) -> MessageRepository:
    """Получить репозиторий сообщений."""
    return MessageRepository(db)