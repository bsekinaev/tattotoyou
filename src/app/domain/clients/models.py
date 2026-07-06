"""
ORM-модели для домена "Клиенты".
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

# 🛡️ Импортируем только для линтеров (mypy/ruff), чтобы избежать Circular Import
if TYPE_CHECKING:
    from app.domain.conversations.models import Conversation


class Platform(Base):
    """
    Платформа (Telegram, VK, Instagram).
    Хранит credentials для каждой соцсети.
    """

    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, comment="Название: telegram, vk, instagram"
    )
    webhook_secret: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Секрет для верификации webhook'ов"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Связь: у платформы может быть много клиентов
    clients: Mapped[list[Client]] = relationship(
        back_populates="platform",
        cascade="all, delete-orphan",
    )


class Client(Base):
    """
    Клиент тату-студии.
    Единый профиль клиента независимо от платформы.
    """

    __tablename__ = "clients"

    __table_args__ = (
        UniqueConstraint("platform_id", "external_id", name="uq_client_platform_external"),
        CheckConstraint(
            "lead_status IN ('new', 'qualification', 'consultation', "
            "'waiting_payment', 'booked', 'completed', 'lost')",
            name="ck_clients_lead_status",
        ),
        Index("ix_clients_lead_status", "lead_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platforms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="ID пользователя в соцсети"
    )
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)

    is_vip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ban_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    lead_status: Mapped[str] = mapped_column(
        String(30),
        default="new",
        server_default="new",
        nullable=False,
        comment="Этап клиента в продуктовой воронке",
    )
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tattoo_idea: Mapped[str | None] = mapped_column(Text, nullable=True)
    placement: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_details: Mapped[str | None] = mapped_column(String(120), nullable=True)
    style_preferences: Mapped[str | None] = mapped_column(String(200), nullable=True)
    budget_details: Mapped[str | None] = mapped_column(String(120), nullable=True)
    desired_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Связи
    platform: Mapped[Platform] = relationship(back_populates="clients")
    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
    )
