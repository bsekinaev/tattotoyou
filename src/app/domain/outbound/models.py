"""ORM-модель надёжной исходящей доставки сообщений и уведомлений."""

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
    Text,
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

OUTBOUND_KIND_MESSAGE = "message"
OUTBOUND_KIND_NOTIFICATION = "notification"

DISPATCHABLE_OUTBOUND_DELIVERY_STATUSES = (
    OUTBOUND_DELIVERY_PENDING,
    OUTBOUND_DELIVERY_RETRYING,
)


class OutboundDelivery(Base):
    """Попытка доставить сохранённый payload во внешнюю платформу."""

    __tablename__ = "outbound_deliveries"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_outbound_deliveries_message_id"),
        UniqueConstraint(
            "deduplication_key",
            name="uq_outbound_deliveries_deduplication_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'retrying', 'sent', 'failed')",
            name="ck_outbound_deliveries_status",
        ),
        CheckConstraint(
            "delivery_kind IN ('message', 'notification')",
            name="ck_outbound_deliveries_kind",
        ),
        CheckConstraint(
            "(delivery_kind = 'message' AND message_id IS NOT NULL AND payload_text IS NULL) "
            "OR (delivery_kind = 'notification' AND message_id IS NULL "
            "AND payload_text IS NOT NULL)",
            name="ck_outbound_deliveries_payload_source",
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
    message_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
    )
    delivery_kind: Mapped[str] = mapped_column(
        String(32),
        default=OUTBOUND_KIND_MESSAGE,
        server_default=OUTBOUND_KIND_MESSAGE,
        nullable=False,
    )
    payload_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    deduplication_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
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
