"""Репозиторий для работы с диалогами."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.clients.models import Client
from app.domain.conversations.models import (
    CONVERSATION_ACTIVE,
    CONVERSATION_CLOSED,
    CONVERSATION_ESCALATED,
    CONVERSATION_HUMAN_OWNED,
    OPEN_CONVERSATION_STATUSES,
    Conversation,
)
from app.infrastructure.db.repository import BaseRepository

ACTIVE_CONVERSATION_TTL = timedelta(hours=24)


class InvalidConversationTransitionError(ValueError):
    """Запрошен недопустимый переход состояния диалога."""


class ConversationRepository(BaseRepository[Conversation]):
    def __init__(self, session):
        super().__init__(Conversation, session)

    async def get_active_by_client(self, client_id: int) -> Conversation | None:
        """Получить открытый диалог клиента, которым ещё управляет AI."""
        cutoff = datetime.now(UTC) - ACTIVE_CONVERSATION_TTL
        result = await self.session.execute(
            select(self.model)
            .where(
                self.model.client_id == client_id,
                self.model.status == CONVERSATION_ACTIVE,
                self.model.last_activity_at > cutoff,
            )
            .order_by(self.model.last_activity_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_or_create_active(self, client_id: int) -> Conversation:
        """Получить единственный открытый диалог или создать новый active.

        Эскалированный или взятый человеком диалог не закрывается по TTL:
        новые сообщения должны оставаться в том же handoff-контексте, а AI —
        молчать до явного возврата управления.
        """
        await self._lock_client(client_id)

        now = datetime.now(UTC)
        conversations = await self._get_open_conversations(client_id)
        current = conversations[0] if conversations else None

        for duplicate in conversations[1:]:
            self._close(duplicate, now)

        if current is not None:
            if current.status != CONVERSATION_ACTIVE or self._is_recent(current, now):
                if len(conversations) > 1:
                    await self.session.flush()
                return current
            self._close(current, now)
            await self.session.flush()

        return await self.create(
            client_id=client_id,
            status=CONVERSATION_ACTIVE,
            assigned_to_human=False,
            last_activity_at=now,
        )

    async def get_locked(self, conversation_id: uuid.UUID) -> Conversation:
        result = await self.session.execute(
            select(self.model).where(self.model.id == conversation_id).with_for_update()
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise LookupError("Conversation does not exist")
        return conversation

    async def escalate(self, conversation: Conversation) -> Conversation:
        """Передать диалог в очередь Сони и остановить AI."""
        if conversation.status == CONVERSATION_ESCALATED:
            return conversation
        if conversation.status != CONVERSATION_ACTIVE:
            raise InvalidConversationTransitionError(
                f"Cannot escalate conversation from {conversation.status}"
            )
        conversation.status = CONVERSATION_ESCALATED
        conversation.assigned_to_human = False
        conversation.closed_at = None
        await self.session.flush()
        return conversation

    async def take_over(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self.get_locked(conversation_id)
        if conversation.status not in (
            CONVERSATION_ACTIVE,
            CONVERSATION_ESCALATED,
            CONVERSATION_HUMAN_OWNED,
        ):
            raise InvalidConversationTransitionError(
                f"Cannot take over conversation from {conversation.status}"
            )
        conversation.status = CONVERSATION_HUMAN_OWNED
        conversation.assigned_to_human = True
        conversation.closed_at = None
        await self.session.flush()
        return conversation

    async def return_to_ai(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self.get_locked(conversation_id)
        if conversation.status not in (
            CONVERSATION_ESCALATED,
            CONVERSATION_HUMAN_OWNED,
        ):
            raise InvalidConversationTransitionError(
                f"Cannot return conversation to AI from {conversation.status}"
            )
        conversation.status = CONVERSATION_ACTIVE
        conversation.assigned_to_human = False
        conversation.closed_at = None
        conversation.last_activity_at = datetime.now(UTC)
        await self.session.flush()
        return conversation

    async def close_conversation(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self.get_locked(conversation_id)
        if conversation.status == CONVERSATION_CLOSED:
            return conversation
        if conversation.status not in OPEN_CONVERSATION_STATUSES:
            raise InvalidConversationTransitionError(
                f"Cannot close conversation from {conversation.status}"
            )
        self._close(conversation, datetime.now(UTC))
        await self.session.flush()
        return conversation

    async def update_activity(self, conversation: Conversation) -> Conversation:
        """Обновить время последней активности."""
        return await self.update(conversation, last_activity_at=datetime.now(UTC))

    async def increment_ai_messages(self, conversation: Conversation) -> None:
        conversation.ai_messages_count += 1
        await self.session.flush()

    async def increment_human_messages(self, conversation: Conversation) -> None:
        conversation.human_messages_count += 1
        await self.session.flush()

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
                self.model.status.in_(OPEN_CONVERSATION_STATUSES),
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
        conversation.status = CONVERSATION_CLOSED
        conversation.assigned_to_human = False
        conversation.closed_at = closed_at
