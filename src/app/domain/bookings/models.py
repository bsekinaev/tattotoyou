"""ORM-модели продуктовой заявки на татуировку и записи на сеанс."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
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

if TYPE_CHECKING:
    from app.domain.clients.models import Client
    from app.domain.conversations.models import Conversation

APPLICATION_NEW = "new"
APPLICATION_QUALIFICATION = "qualification"
APPLICATION_CONSULTATION = "consultation"
APPLICATION_AWAITING_DEPOSIT = "awaiting_deposit"
APPLICATION_BOOKED = "booked"
APPLICATION_COMPLETED = "completed"
APPLICATION_CANCELED = "canceled"
APPLICATION_REJECTED = "rejected"

APPLICATION_STATUSES = (
    APPLICATION_NEW,
    APPLICATION_QUALIFICATION,
    APPLICATION_CONSULTATION,
    APPLICATION_AWAITING_DEPOSIT,
    APPLICATION_BOOKED,
    APPLICATION_COMPLETED,
    APPLICATION_CANCELED,
    APPLICATION_REJECTED,
)
ACTIVE_APPLICATION_STATUSES = (
    APPLICATION_NEW,
    APPLICATION_QUALIFICATION,
    APPLICATION_CONSULTATION,
    APPLICATION_AWAITING_DEPOSIT,
    APPLICATION_BOOKED,
)

COLOR_BLACK_AND_GREY = "black_and_grey"
COLOR_COLOR = "color"
COLOR_UNDECIDED = "undecided"
COLOR_MODES = (COLOR_BLACK_AND_GREY, COLOR_COLOR, COLOR_UNDECIDED)

REFERENCE_TELEGRAM_FILE = "telegram_file"
REFERENCE_URL = "url"
REFERENCE_TYPES = (REFERENCE_TELEGRAM_FILE, REFERENCE_URL)

APPOINTMENT_DRAFT = "draft"
APPOINTMENT_PENDING = "pending"
APPOINTMENT_CONFIRMED = "confirmed"
APPOINTMENT_COMPLETED = "completed"
APPOINTMENT_CANCELED = "canceled"
APPOINTMENT_STATUSES = (
    APPOINTMENT_DRAFT,
    APPOINTMENT_PENDING,
    APPOINTMENT_CONFIRMED,
    APPOINTMENT_COMPLETED,
    APPOINTMENT_CANCELED,
)

DEPOSIT_NOT_REQUIRED = "not_required"
DEPOSIT_PENDING = "pending"
DEPOSIT_PAID = "paid"
DEPOSIT_REFUNDED = "refunded"
DEPOSIT_STATUSES = (
    DEPOSIT_NOT_REQUIRED,
    DEPOSIT_PENDING,
    DEPOSIT_PAID,
    DEPOSIT_REFUNDED,
)


class TattooApplication(Base):
    """Отдельный тату-проект клиента со своей продуктовой воронкой."""

    __tablename__ = "tattoo_applications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'qualification', 'consultation', 'awaiting_deposit', "
            "'booked', 'completed', 'canceled', 'rejected')",
            name="ck_tattoo_applications_status",
        ),
        CheckConstraint(
            "color_mode IN ('black_and_grey', 'color', 'undecided')",
            name="ck_tattoo_applications_color_mode",
        ),
        CheckConstraint(
            "budget_min IS NULL OR budget_min >= 0",
            name="ck_tattoo_applications_budget_min_nonnegative",
        ),
        CheckConstraint(
            "budget_max IS NULL OR budget_max >= 0",
            name="ck_tattoo_applications_budget_max_nonnegative",
        ),
        CheckConstraint(
            "budget_min IS NULL OR budget_max IS NULL OR budget_max >= budget_min",
            name="ck_tattoo_applications_budget_range",
        ),
        Index("ix_tattoo_applications_client_status", "client_id", "status"),
        Index("ix_tattoo_applications_desired_date", "desired_date"),
        Index(
            "uq_tattoo_applications_one_open_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "conversation_id IS NOT NULL AND status IN "
                "('new', 'qualification', 'consultation', 'awaiting_deposit', 'booked')"
            ),
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
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default=APPLICATION_NEW,
        server_default=APPLICATION_NEW,
        nullable=False,
    )
    idea: Mapped[str | None] = mapped_column(Text, nullable=True)
    placement: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_details: Mapped[str | None] = mapped_column(String(120), nullable=True)
    color_mode: Mapped[str] = mapped_column(
        String(20),
        default=COLOR_UNDECIDED,
        server_default=COLOR_UNDECIDED,
        nullable=False,
    )
    style_preferences: Mapped[str | None] = mapped_column(String(200), nullable=True)
    budget_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    desired_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    client_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client] = relationship(back_populates="tattoo_applications")
    conversation: Mapped[Conversation | None] = relationship(back_populates="tattoo_applications")
    references: Mapped[list[ApplicationReference]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationReference.created_at",
    )
    status_history: Mapped[list[ApplicationStatusHistory]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="ApplicationStatusHistory.created_at",
    )
    appointment: Mapped[Appointment | None] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ApplicationReference(Base):
    """Референс проекта: Telegram file_id или публичная ссылка."""

    __tablename__ = "application_references"
    __table_args__ = (
        CheckConstraint(
            "reference_type IN ('telegram_file', 'url')",
            name="ck_application_references_type",
        ),
        Index("ix_application_references_application", "application_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tattoo_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    reference_type: Mapped[str] = mapped_column(String(30), nullable=False)
    value: Mapped[str] = mapped_column(String(2048), nullable=False)
    file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    application: Mapped[TattooApplication] = relationship(back_populates="references")


class ApplicationStatusHistory(Base):
    """Неизменяемый журнал переходов заявки между этапами."""

    __tablename__ = "application_status_history"
    __table_args__ = (
        CheckConstraint(
            "to_status IN ('new', 'qualification', 'consultation', 'awaiting_deposit', "
            "'booked', 'completed', 'canceled', 'rejected')",
            name="ck_application_status_history_to_status",
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('new', 'qualification', 'consultation', 'awaiting_deposit', "
            "'booked', 'completed', 'canceled', 'rejected')",
            name="ck_application_status_history_from_status",
        ),
        CheckConstraint(
            "actor IN ('studio', 'system')",
            name="ck_application_status_history_actor",
        ),
        Index("ix_application_status_history_application", "application_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tattoo_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[str] = mapped_column(
        String(20),
        default="studio",
        server_default="studio",
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    application: Mapped[TattooApplication] = relationship(back_populates="status_history")


class Appointment(Base):
    """Запланированный сеанс по заявке."""

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'pending', 'confirmed', 'completed', 'canceled')",
            name="ck_appointments_status",
        ),
        CheckConstraint(
            "deposit_status IN ('not_required', 'pending', 'paid', 'refunded')",
            name="ck_appointments_deposit_status",
        ),
        CheckConstraint(
            "scheduled_end > scheduled_start",
            name="ck_appointments_positive_period",
        ),
        CheckConstraint(
            "duration_minutes > 0 AND duration_minutes <= 1440",
            name="ck_appointments_duration",
        ),
        CheckConstraint(
            "quoted_price IS NULL OR quoted_price >= 0",
            name="ck_appointments_quoted_price_nonnegative",
        ),
        CheckConstraint(
            "deposit_amount IS NULL OR deposit_amount >= 0",
            name="ck_appointments_deposit_amount_nonnegative",
        ),
        Index("ix_appointments_status_start", "status", "scheduled_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tattoo_applications.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    quoted_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deposit_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deposit_status: Mapped[str] = mapped_column(
        String(20),
        default=DEPOSIT_NOT_REQUIRED,
        server_default=DEPOSIT_NOT_REQUIRED,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=APPOINTMENT_DRAFT,
        server_default=APPOINTMENT_DRAFT,
        nullable=False,
    )
    canceled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    application: Mapped[TattooApplication] = relationship(back_populates="appointment")
