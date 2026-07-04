"""Репозиторий Transactional Outbox исходящих сообщений."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.domain.outbound.models import (
    DISPATCHABLE_OUTBOUND_DELIVERY_STATUSES,
    OUTBOUND_DELIVERY_FAILED,
    OUTBOUND_DELIVERY_PENDING,
    OUTBOUND_DELIVERY_RETRYING,
    OUTBOUND_DELIVERY_SENDING,
    OUTBOUND_DELIVERY_SENT,
    OutboundDelivery,
)
from app.infrastructure.db.repository import BaseRepository


class OutboundDeliveryRepository(BaseRepository[OutboundDelivery]):
    """Хранилище доставок с атомарным захватом и повторными попытками."""

    def __init__(self, session):
        super().__init__(OutboundDelivery, session)

    async def get_by_uuid(self, delivery_id: uuid.UUID) -> OutboundDelivery | None:
        result = await self.session.execute(select(self.model).where(self.model.id == delivery_id))
        return result.scalar_one_or_none()

    async def get_by_message_id(self, message_id: int) -> OutboundDelivery | None:
        result = await self.session.execute(
            select(self.model).where(self.model.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def create_for_message(
        self,
        *,
        message_id: int,
        platform: str,
        destination_id: str,
    ) -> tuple[OutboundDelivery, bool]:
        """Создать одну доставку на исходящее сообщение."""
        statement = (
            insert(self.model)
            .values(
                message_id=message_id,
                platform=platform,
                destination_id=destination_id,
                status=OUTBOUND_DELIVERY_PENDING,
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[self.model.message_id])
            .returning(self.model.id)
        )
        result = await self.session.execute(statement)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            delivery = await self.get_by_uuid(inserted_id)
            created = True
        else:
            delivery = await self.get_by_message_id(message_id)
            created = False

        if delivery is None:
            raise RuntimeError("Outbound delivery upsert completed without a visible row")
        return delivery, created

    async def claim(
        self,
        delivery_id: uuid.UUID,
        *,
        processing_timeout: timedelta,
        now: datetime | None = None,
    ) -> OutboundDelivery | None:
        """Атомарно захватить готовую или зависшую доставку."""
        current_time = now or datetime.now(UTC)
        stale_before = current_time - processing_timeout
        due_delivery = and_(
            self.model.status.in_(DISPATCHABLE_OUTBOUND_DELIVERY_STATUSES),
            or_(
                self.model.next_attempt_at.is_(None),
                self.model.next_attempt_at <= current_time,
            ),
        )
        stale_sending = and_(
            self.model.status == OUTBOUND_DELIVERY_SENDING,
            or_(
                self.model.processing_started_at.is_(None),
                self.model.processing_started_at <= stale_before,
            ),
        )
        result = await self.session.execute(
            select(self.model)
            .where(self.model.id == delivery_id, or_(due_delivery, stale_sending))
            .with_for_update(skip_locked=True)
        )
        delivery = result.scalar_one_or_none()
        if delivery is None:
            return None

        delivery.status = OUTBOUND_DELIVERY_SENDING
        delivery.attempts += 1
        delivery.processing_started_at = current_time
        delivery.next_attempt_at = None
        delivery.last_error_code = None
        await self.session.flush()
        return delivery

    async def mark_sent(
        self,
        delivery_id: uuid.UUID,
        *,
        platform_message_id: str,
        now: datetime | None = None,
    ) -> OutboundDelivery:
        current_time = now or datetime.now(UTC)
        delivery = await self._get_locked(delivery_id)
        delivery.status = OUTBOUND_DELIVERY_SENT
        delivery.platform_message_id = platform_message_id
        delivery.sent_at = current_time
        delivery.failed_at = None
        delivery.processing_started_at = None
        delivery.next_attempt_at = None
        delivery.last_error_code = None
        await self.session.flush()
        return delivery

    async def mark_failure(
        self,
        delivery_id: uuid.UUID,
        *,
        error_code: str,
        max_attempts: int,
        retry_delay: timedelta,
        permanent: bool,
        now: datetime | None = None,
    ) -> OutboundDelivery:
        current_time = now or datetime.now(UTC)
        delivery = await self._get_locked(delivery_id)
        exhausted = delivery.attempts >= max_attempts
        delivery.last_error_code = error_code[:100]
        delivery.processing_started_at = None

        if permanent or exhausted:
            delivery.status = OUTBOUND_DELIVERY_FAILED
            delivery.next_attempt_at = None
            delivery.failed_at = current_time
        else:
            delivery.status = OUTBOUND_DELIVERY_RETRYING
            delivery.next_attempt_at = current_time + retry_delay
            delivery.failed_at = None

        await self.session.flush()
        return delivery

    async def list_dispatchable_ids(
        self,
        *,
        limit: int,
        processing_timeout: timedelta,
        now: datetime | None = None,
    ) -> list[uuid.UUID]:
        current_time = now or datetime.now(UTC)
        stale_before = current_time - processing_timeout
        due_delivery = and_(
            self.model.status.in_(DISPATCHABLE_OUTBOUND_DELIVERY_STATUSES),
            or_(
                self.model.next_attempt_at.is_(None),
                self.model.next_attempt_at <= current_time,
            ),
        )
        stale_sending = and_(
            self.model.status == OUTBOUND_DELIVERY_SENDING,
            or_(
                self.model.processing_started_at.is_(None),
                self.model.processing_started_at <= stale_before,
            ),
        )
        result = await self.session.execute(
            select(self.model.id)
            .where(or_(due_delivery, stale_sending))
            .order_by(self.model.created_at.asc(), self.model.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def retry_failed(self, delivery_id: uuid.UUID) -> OutboundDelivery:
        """Вернуть failed-доставку в pending для ручного повтора."""
        delivery = await self._get_locked(delivery_id)
        if delivery.status != OUTBOUND_DELIVERY_FAILED:
            raise ValueError("Only failed deliveries can be retried manually")
        delivery.status = OUTBOUND_DELIVERY_PENDING
        delivery.attempts = 0
        delivery.next_attempt_at = None
        delivery.processing_started_at = None
        delivery.last_error_code = None
        delivery.failed_at = None
        await self.session.flush()
        return delivery

    async def _get_locked(self, delivery_id: uuid.UUID) -> OutboundDelivery:
        result = await self.session.execute(
            select(self.model).where(self.model.id == delivery_id).with_for_update()
        )
        delivery = result.scalar_one_or_none()
        if delivery is None:
            raise LookupError("Outbound delivery does not exist")
        return delivery
