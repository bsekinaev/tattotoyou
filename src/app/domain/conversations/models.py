"""
ORM-модели для домена "Диалоги".
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class Conversation(Base):
    """
    Диалог (сессия общения) с клиентом.

    Один диалог = одна непрерывная беседа.
    Если клиент написал, мы ответили, он пропал на неделю и написал снова —
    это может быть как новый диалог, так и продолжение старого (решим на уровне логики).
    """
    __tablename__ = "conversations"

    # UUID вместо int — лучшая практика для distributed systems
    # (не раскрывает количество записей, безопаснее в URL)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    client_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
        comment="active, escalated, closed, spam"
    )
    assigned_to_human: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="Взял ли мастер диалог на себя"
    )

    # Статистика
    ai_messages_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    human_messages_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Временные метки
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,  # Индекс для сортировки "последние активные"
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Связи
    client: Mapped["Client"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """
    Отдельное сообщение в диалоге.

    Append-only лог: сообщения никогда не удаляются и не изменяются.
    Это важно для аудита и обучения AI.
    """
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False,
        comment="inbound (от клиента) или outbound (от бота)"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Метаданные от AI
    ai_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Флаги
    is_escalation_trigger: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False,
        comment="Это сообщение спровоцировало эскалацию"
    )

    # Связи
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")