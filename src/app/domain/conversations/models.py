"""
ORM-модели для домена "Диалоги".
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

# 🛡️ Импортируем только для линтеров (mypy/ruff), чтобы избежать Circular Import
if TYPE_CHECKING:
    from app.domain.clients.models import Client


CONVERSATION_ACTIVE = "active"
CONVERSATION_ESCALATED = "escalated"
CONVERSATION_HUMAN_OWNED = "human_owned"
CONVERSATION_CLOSED = "closed"
CONVERSATION_SPAM = "spam"

OPEN_CONVERSATION_STATUSES = (
    CONVERSATION_ACTIVE,
    CONVERSATION_ESCALATED,
    CONVERSATION_HUMAN_OWNED,
)


class Conversation(Base):
    """
    Диалог (сессия общения) с клиентом.
    """

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'escalated', 'human_owned', 'closed', 'spam')",
            name="ck_conversations_status",
        ),
        Index(
            "uq_conversations_one_active_per_client",
            "client_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'escalated', 'human_owned')"),
        ),
    )

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
        default=CONVERSATION_ACTIVE,
        server_default=CONVERSATION_ACTIVE,
        nullable=False,
        comment="active, escalated, human_owned, closed, spam",
    )
    assigned_to_human: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="Взял ли мастер диалог на себя"
    )

    ai_messages_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    human_messages_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="Когда Соня последний раз открывала диалог в панели",
    )

    # Связи
    client: Mapped[Client] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """
    Отдельное сообщение в диалоге.
    Append-only лог: сообщения никогда не удаляются и не изменяются.
    """

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "sender_type IN ('client', 'bot', 'human', 'system')",
            name="ck_messages_sender_type",
        ),
        Index(
            "uq_messages_causation_direction",
            "causation_event_id",
            "direction",
            unique=True,
            postgresql_where=text("causation_event_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="inbound (от клиента) или outbound (от бота)"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sender_type: Mapped[str] = mapped_column(
        String(20),
        default="bot",
        server_default="bot",
        nullable=False,
        comment="client, bot, human, system",
    )

    platform_message_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
        comment="ID сообщения в самой платформе (например, в Telegram)",
    )

    causation_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("incoming_events.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        comment="Входящее событие, которое вызвало это сообщение",
    )

    ai_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_escalation_trigger: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="Это сообщение спровоцировало эскалацию"
    )

    # Связи
    conversation: Mapped[Conversation] = relationship(back_populates="messages")
