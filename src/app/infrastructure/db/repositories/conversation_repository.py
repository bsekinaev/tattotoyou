"""Репозиторий для работы с диалогами."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.clients.models import Client
from app.domain.conversations.models import Conversation
from app.infrastructure.db.repository import BaseRepository

ACTIVE_CONVERSATION_TTL = timedelta(hours=24)


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, session):
        super().__init__(Conversation, session)

    async def get_active_by_client(self, client_id: int) -> Conversation | None:
        """Получить недавний активный диалог клиента."""
        cutoff = datetime.now(UTC) - ACTIVE_CONVERSATION_TTL
        result = await self.session.execute(
            select(self.model)
            .where(
                self.model.client_id == client_id,
                self.model.status == "active",
                self.model.last_activity_at > cutoff,
            )
            .order_by(self.model.last_activity_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_or_create_active(self, client_id: int) -> Conversation:
        """Атомарно получить единственный активный диалог клиента.

        Блокировка строки клиента сериализует создание диалогов только для
        одного клиента. Частичный уникальный индекс в PostgreSQL дополнительно
        защищает инвариант от обхода репозитория.
        """
        await self._lock_client(client_id)

        now = datetime.now(UTC)
        conversations = await self._get_open_conversations(client_id)
        current = conversations[0] if conversations else None

        # Самовосстановление старых данных: закрываем все лишние active-записи.
        for duplicate in conversations[1:]:
            self._close(duplicate, now)

        if current is not None and self._is_recent(current, now):
            if len(conversations) > 1:
                await self.session.flush()
            return current

        if current is not None:
            self._close(current, now)
            await self.session.flush()

        return await self.create(
            client_id=client_id,
            status="active",
            last_activity_at=now,
        )

    async def update_activity(self, conversation: Conversation) -> Conversation:
        """Обновить время последней активности."""
        return await self.update(conversation, last_activity_at=datetime.now(UTC))

    async def _lock_client(self, client_id: int) -> None:
        result = await self.session.execute(
            select(Client.id).where(Client.id == client_id).with_for_update()
        )
        if result.scalar_one_or_none() is None:
            raise LookupError("Client does not exist")

    async def _get_open_conversations(self, client_id: int) -> list[Conversation]:
        result = await self.session.execute(
            select(self.model)
            .where(
                self.model.client_id == client_id,
                self.model.status == "active",
            )
            .order_by(
                self.model.last_activity_at.desc(),
                self.model.created_at.desc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def _is_recent(conversation: Conversation, now: datetime) -> bool:
        last_activity = conversation.last_activity_at
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=UTC)
        return last_activity > now - ACTIVE_CONVERSATION_TTL

    @staticmethod
    def _close(conversation: Conversation, closed_at: datetime) -> None:
        conversation.status = "closed"
        conversation.closed_at = closed_at
