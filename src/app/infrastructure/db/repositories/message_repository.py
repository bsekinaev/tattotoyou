"""Репозиторий для работы с сообщениями."""

from sqlalchemy import select

from app.domain.conversations.models import Message
from app.infrastructure.db.repository import BaseRepository


class MessageRepository(BaseRepository[Message]):
    def __init__(self, session):
        super().__init__(Message, session)

    async def get_by_conversation(self, conversation_id: str, limit: int = 50) -> list[Message]:
        """Получить последние сообщения диалога."""
        result = await self.session.execute(
            select(self.model)
            .where(self.model.conversation_id == conversation_id)
            .order_by(self.model.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_message(
        self,
        conversation_id: str,
        direction: str,
        content: str,
        platform_message_id: str | None = None,
        **kwargs,
    ) -> Message:
        """Создать новое сообщение."""
        return await self.create(
            conversation_id=conversation_id,
            direction=direction,
            content=content,
            platform_message_id=platform_message_id,
            **kwargs,
        )

    async def get_history(self, conversation_id: str, limit: int = 10) -> list[Message]:
        """Получить последние N сообщений диалога в хронологическом порядке."""
        result = await self.session.execute(
            select(self.model)
            .where(self.model.conversation_id == conversation_id)
            .order_by(self.model.created_at.desc())
            .limit(limit)
        )
        # Разворачиваем список, чтобы история шла от старого к новому (для LLM)
        return list(reversed(result.scalars().all()))