"""Репозиторий долговечных входящих событий."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.domain.incoming.models import (
    DISPATCHABLE_INCOMING_EVENT_STATUSES,
    INCOMING_EVENT_FAILED,
    INCOMING_EVENT_PENDING,
    INCOMING_EVENT_PROCESSED,
    INCOMING_EVENT_PROCESSING,
    INCOMING_EVENT_RETRYING,
    IncomingEvent,
)
from app.infrastructure.db.repository import BaseRepository


class IncomingEventRepository(BaseRepository[IncomingEvent]):
    """Хранилище Inbox с атомарной дедупликацией и захватом обработки."""

    def __init__(self, session):
        super().__init__(IncomingEvent, session)

    async def get_by_uuid(self, event_id: uuid.UUID) -> IncomingEvent | None:
        result = await self.session.execute(select(self.model).where(self.model.id == event_id))
        return result.scalar_one_or_none()

    async def get_by_external_id(
        self,
        *,
        platform: str,
        external_event_id: str,
    ) -> IncomingEvent | None:
        result = await self.session.execute(
            select(self.model).where(
                self.model.platform == platform,
                self.model.external_event_id == external_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        *,
        platform: str,
        external_event_id: str,
        payload: dict[str, Any],
    ) -> tuple[IncomingEvent, bool]:
        """Создать событие один раз или вернуть уже принятое событие."""
        statement = (
            insert(self.model)
            .values(
                platform=platform,
                external_event_id=external_event_id,
                payload=payload,
                status=INCOMING_EVENT_PENDING,
                attempts=0,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    self.model.platform,
                    self.model.external_event_id,
                ]
            )
            .returning(self.model.id)
        )
        result = await self.session.execute(statement)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            event = await self.get_by_uuid(inserted_id)
            created = True
        else:
            event = await self.get_by_external_id(
                platform=platform,
                external_event_id=external_event_id,
            )
            created = False

        if event is None:
            raise RuntimeError("Incoming event upsert completed without a visible row")
        return event, created

    async def claim(
        self,
        event_id: uuid.UUID,
        *,
        processing_timeout: timedelta,
        now: datetime | None = None,
    ) -> IncomingEvent | None:
        """Атомарно захватить готовое или зависшее событие для обработки."""
        current_time = now or datetime.now(UTC)
        stale_before = current_time - processing_timeout

        due_event = and_(
            self.model.status.in_(DISPATCHABLE_INCOMING_EVENT_STATUSES),
            or_(
                self.model.next_attempt_at.is_(None),
                self.model.next_attempt_at <= current_time,
            ),
        )
        stale_processing = and_(
            self.model.status == INCOMING_EVENT_PROCESSING,
            or_(
                self.model.processing_started_at.is_(None),
                self.model.processing_started_at <= stale_before,
            ),
        )

        result = await self.session.execute(
            select(self.model)
            .where(
                self.model.id == event_id,
                or_(due_event, stale_processing),
            )
            .with_for_update(skip_locked=True)
        )
        event = result.scalar_one_or_none()
        if event is None:
            return None

        event.status = INCOMING_EVENT_PROCESSING
        event.attempts += 1
        event.processing_started_at = current_time
        event.next_attempt_at = None
        event.last_error_code = None
        await self.session.flush()
        return event

    async def mark_processed(
        self,
        event_id: uuid.UUID,
        *,
        now: datetime | None = None,
    ) -> IncomingEvent:
        event = await self._get_locked(event_id)
        current_time = now or datetime.now(UTC)
        event.status = INCOMING_EVENT_PROCESSED
        event.processed_at = current_time
        event.processing_started_at = None
        event.next_attempt_at = None
        event.last_error_code = None
        await self.session.flush()
        return event

    async def mark_failure(
        self,
        event_id: uuid.UUID,
        *,
        error_code: str,
        max_attempts: int,
        retry_delay: timedelta,
        now: datetime | None = None,
    ) -> IncomingEvent:
        event = await self._get_locked(event_id)
        current_time = now or datetime.now(UTC)
        event.processing_started_at = None
        event.last_error_code = error_code[:100]

        if event.attempts >= max_attempts:
            event.status = INCOMING_EVENT_FAILED
            event.next_attempt_at = None
        else:
            event.status = INCOMING_EVENT_RETRYING
            event.next_attempt_at = current_time + retry_delay

        await self.session.flush()
        return event

    async def list_dispatchable_ids(
        self,
        *,
        limit: int,
        processing_timeout: timedelta,
        now: datetime | None = None,
    ) -> list[uuid.UUID]:
        """Найти pending/retrying и зависшие processing-события для recovery."""
        current_time = now or datetime.now(UTC)
        stale_before = current_time - processing_timeout

        due_event = and_(
            self.model.status.in_(DISPATCHABLE_INCOMING_EVENT_STATUSES),
            or_(
                self.model.next_attempt_at.is_(None),
                self.model.next_attempt_at <= current_time,
            ),
        )
        stale_processing = and_(
            self.model.status == INCOMING_EVENT_PROCESSING,
            or_(
                self.model.processing_started_at.is_(None),
                self.model.processing_started_at <= stale_before,
            ),
        )

        result = await self.session.execute(
            select(self.model.id)
            .where(or_(due_event, stale_processing))
            .order_by(self.model.received_at.asc(), self.model.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def _get_locked(self, event_id: uuid.UUID) -> IncomingEvent:
        result = await self.session.execute(
            select(self.model).where(self.model.id == event_id).with_for_update()
        )
        event = result.scalar_one_or_none()
        if event is None:
            raise LookupError("Incoming event does not exist")
        return event
