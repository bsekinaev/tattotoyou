"""
ORM-модели для домена "Клиенты".
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class Platform(Base):
    """
    Платформа (Telegram, VK, Instagram).

    Хранит credentials для каждой соцсети.
    """
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False,
        comment="Название: telegram, vk, instagram"
    )
    webhook_secret: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="Секрет для верификации webhook'ов"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    # Связь: у платформы может быть много клиентов
    clients: Mapped[list["Client"]] = relationship(
        back_populates="platform",
        cascade="all, delete-orphan",
    )


class Client(Base):
    """
    Клиент тату-студии.

    Единый профиль клиента независимо от платформы.
    Если один человек напишет и в TG, и в VK — это будут ДВА разных Client
    """
    __tablename__ = "clients"

    # Уникальность: на одной платформе не может быть двух клиентов с одним external_id
    __table_args__ = (
        UniqueConstraint("platform_id", "external_id", name="uq_client_platform_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("platforms.id", ondelete="CASCADE"),
        nullable=False,
        index=True,  # Индекс для быстрого поиска клиентов по платформе
    )
    external_id: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="ID пользователя в соцсети"
    )
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Бизнес-атрибуты
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ban_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Связи
    platform: Mapped["Platform"] = relationship(back_populates="clients")
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
    )