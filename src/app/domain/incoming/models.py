"""ORM-модель долговечного журнала входящих событий."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base

INCOMING_EVENT_PENDING = "pending"
INCOMING_EVENT_PROCESSING = "processing"
INCOMING_EVENT_RETRYING = "retrying"
INCOMING_EVENT_PROCESSED = "processed"
INCOMING_EVENT_FAILED = "failed"

DISPATCHABLE_INCOMING_EVENT_STATUSES = (
    INCOMING_EVENT_PENDING,
    INCOMING_EVENT_RETRYING,
)


class IncomingEvent(Base):
    """Долговечное входящее событие, принятое от внешней платформы."""

    __tablename__ = "incoming_events"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "external_event_id",
            name="uq_incoming_events_platform_external",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'retrying', 'processed', 'failed')",
            name="ck_incoming_events_status",
        ),
        Index(
            "ix_incoming_events_dispatch",
            "status",
            "next_attempt_at",
            "received_at",
        ),
        Index("ix_incoming_events_processing_started_at", "processing_started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=INCOMING_EVENT_PENDING,
        server_default=INCOMING_EVENT_PENDING,
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
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
