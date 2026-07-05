"""Репозиторий для работы с сообщениями."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

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

    async def get_by_causation(
        self,
        causation_event_id: uuid.UUID,
        direction: str,
    ) -> Message | None:
        """Получить единственный эффект события для указанного направления."""
        result = await self.session.execute(
            select(self.model).where(
                self.model.causation_event_id == causation_event_id,
                self.model.direction == direction,
            )
        )
        return result.scalar_one_or_none()

    async def create_message(
        self,
        conversation_id: str,
        direction: str,
        content: str,
        platform_message_id: str | None = None,
        causation_event_id: uuid.UUID | None = None,
        sender_type: str | None = None,
        **kwargs: Any,
    ) -> Message:
        """Создать новое сообщение с корректным типом автора."""
        return await self.create(
            conversation_id=conversation_id,
            direction=direction,
            content=content,
            sender_type=sender_type or self._default_sender_type(direction),
            platform_message_id=platform_message_id,
            causation_event_id=causation_event_id,
            **kwargs,
        )

    async def create_message_once(
        self,
        *,
        conversation_id: str,
        direction: str,
        content: str,
        causation_event_id: uuid.UUID,
        platform_message_id: str | None = None,
        sender_type: str | None = None,
        **kwargs: Any,
    ) -> tuple[Message, bool]:
        """Создать один inbound/outbound-эффект на входящее событие.

        Частичный уникальный индекс ``(causation_event_id, direction)`` не
        позволяет повторному Celery-запуску записать второй бизнес-эффект.
        """
        values = {
            "conversation_id": conversation_id,
            "direction": direction,
            "content": content,
            "sender_type": sender_type or self._default_sender_type(direction),
            "platform_message_id": platform_message_id,
            "causation_event_id": causation_event_id,
            **kwargs,
        }
        values.setdefault("is_escalation_trigger", False)

        statement = (
            insert(self.model)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    self.model.causation_event_id,
                    self.model.direction,
                ],
                index_where=self.model.causation_event_id.is_not(None),
            )
            .returning(self.model.id)
        )
        result = await self.session.execute(statement)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            message = await self.get_by_id(inserted_id)
            created = True
        else:
            message = await self.get_by_causation(causation_event_id, direction)
            created = False

        if message is None:
            raise RuntimeError("Message upsert completed without a visible row")
        return message, created

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

    @staticmethod
    def _default_sender_type(direction: str) -> str:
        if direction == "inbound":
            return "client"
        if direction == "outbound":
            return "bot"
        raise ValueError(f"Unsupported message direction: {direction}")
