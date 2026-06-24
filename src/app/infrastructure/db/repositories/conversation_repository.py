"""Репозиторий для работы с диалогами."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.conversations.models import Conversation
from app.infrastructure.db.repository import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, session):
        super().__init__(Conversation, session)

    async def get_active_by_client(self, client_id: int) -> Conversation | None:
        """
        Получить активный диалог клиента.
        Диалог считается активным, если была активность за последние 24 часа.
        """
        day_ago = datetime.now(UTC) - timedelta(hours=24)
        result = await self.session.execute(
            select(self.model)
            .where(
                self.model.client_id == client_id,
                self.model.status == "active",
                self.model.last_activity_at > day_ago,
            )
            .order_by(self.model.last_activity_at.desc())
        )
        return result.scalar_one_or_none()

    async def get_or_create_active(self, client_id: int) -> Conversation:
        """
        Get-or-Create для активного диалога.
        """
        conversation = await self.get_active_by_client(client_id)
        if not conversation:
            conversation = await self.create(
                client_id=client_id, status="active", last_activity_at=datetime.now(UTC)
            )
        return conversation

    async def update_activity(self, conversation: Conversation) -> Conversation:
        """Обновить время последней активности."""
        return await self.update(conversation, last_activity_at=datetime.now(UTC))