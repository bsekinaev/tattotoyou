"""ORM-модель надёжной исходящей доставки сообщений."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base

OUTBOUND_DELIVERY_PENDING = "pending"
OUTBOUND_DELIVERY_SENDING = "sending"
OUTBOUND_DELIVERY_RETRYING = "retrying"
OUTBOUND_DELIVERY_SENT = "sent"
OUTBOUND_DELIVERY_FAILED = "failed"

DISPATCHABLE_OUTBOUND_DELIVERY_STATUSES = (
    OUTBOUND_DELIVERY_PENDING,
    OUTBOUND_DELIVERY_RETRYING,
)


class OutboundDelivery(Base):
    """Попытка доставить сохранённое исходящее сообщение во внешнюю платформу."""

    __tablename__ = "outbound_deliveries"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_outbound_deliveries_message_id"),
        CheckConstraint(
            "status IN ('pending', 'sending', 'retrying', 'sent', 'failed')",
            name="ck_outbound_deliveries_status",
        ),
        Index(
            "ix_outbound_deliveries_dispatch",
            "status",
            "next_attempt_at",
            "created_at",
        ),
        Index(
            "ix_outbound_deliveries_processing_started_at",
            "processing_started_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    destination_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=OUTBOUND_DELIVERY_PENDING,
        server_default=OUTBOUND_DELIVERY_PENDING,
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
