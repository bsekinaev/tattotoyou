"""Celery-задачи надёжной доставки Transactional Outbox."""

from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.platforms.exceptions import PlatformHTTPError, PlatformTransportError
from app.core.platforms.telegram_adapter import TelegramAdapter
from app.domain.outbound.models import (
    OUTBOUND_DELIVERY_FAILED,
    OUTBOUND_DELIVERY_RETRYING,
    OUTBOUND_DELIVERY_SENT,
)
from app.infrastructure.db.repositories import (
    MessageRepository,
    OutboundDeliveryRepository,
)
from app.infrastructure.db.session import async_session_factory
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
settings = get_settings()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@dataclass(slots=True)
class DeliveryOutcome:
    status: str
    attempts: int = 0
    error: Exception | None = None


@celery_app.task(
    bind=True,
    name="deliver_outbound_message",
    max_retries=settings.outbound_delivery_max_attempts,
)
def deliver_outbound_message_task(self, delivery_reference: str):
    """Доставить одну запись Outbox и сохранить результат в PostgreSQL."""
    delivery_id = uuid.UUID(str(delivery_reference))
    try:
        outcome = asyncio.run(_deliver_outbound(delivery_id))
    except Exception as exc:
        logger.exception(
            "outbound_delivery_task_infrastructure_failed",
            delivery_id=str(delivery_id),
            error_type=type(exc).__name__,
        )
        raise self.retry(
            exc=exc,
            countdown=_retry_delay_seconds(self.request.retries + 1),
        ) from exc

    if outcome.status == OUTBOUND_DELIVERY_RETRYING:
        logger.warning(
            "outbound_delivery_retry_scheduled",
            delivery_id=str(delivery_id),
            attempts=outcome.attempts,
            error_type=type(outcome.error).__name__ if outcome.error else None,
        )
        raise self.retry(
            exc=outcome.error,
            countdown=_retry_delay_seconds(outcome.attempts),
        )

    if outcome.status == OUTBOUND_DELIVERY_FAILED:
        logger.error(
            "outbound_delivery_permanently_failed",
            delivery_id=str(delivery_id),
            attempts=outcome.attempts,
        )

    return {"status": outcome.status, "delivery_id": str(delivery_id)}


@celery_app.task(name="recover_outbound_deliveries")
def recover_outbound_deliveries_task() -> dict[str, int]:
    """Повторно поставить готовые и зависшие Outbox-доставки в очередь."""
    delivery_ids = asyncio.run(_list_recoverable_delivery_ids())
    dispatched = 0
    for delivery_id in delivery_ids:
        try:
            deliver_outbound_message_task.delay(str(delivery_id))
            dispatched += 1
        except Exception as exc:
            logger.exception(
                "outbound_delivery_recovery_dispatch_failed",
                delivery_id=str(delivery_id),
                error_type=type(exc).__name__,
            )

    logger.info(
        "outbound_delivery_recovery_completed",
        found=len(delivery_ids),
        dispatched=dispatched,
    )
    return {"found": len(delivery_ids), "dispatched": dispatched}


async def _deliver_outbound(delivery_id: uuid.UUID) -> DeliveryOutcome:
    processing_timeout = timedelta(seconds=settings.outbound_delivery_processing_timeout_seconds)
    async with async_session_factory() as db:
        delivery = await OutboundDeliveryRepository(db).claim(
            delivery_id,
            processing_timeout=processing_timeout,
        )
        if delivery is None:
            await db.rollback()
            return DeliveryOutcome(status="ignored")

        message = await MessageRepository(db).get_by_id(delivery.message_id)
        if message is None:
            await OutboundDeliveryRepository(db).mark_failure(
                delivery_id,
                error_code="MessageNotFound",
                max_attempts=settings.outbound_delivery_max_attempts,
                retry_delay=timedelta(0),
                permanent=True,
            )
            await db.commit()
            return DeliveryOutcome(
                status=OUTBOUND_DELIVERY_FAILED,
                attempts=delivery.attempts,
            )

        platform = delivery.platform
        destination_id = delivery.destination_id
        content = message.content
        attempts = delivery.attempts
        await db.commit()

    try:
        platform_message_id = await _send_to_platform(
            platform=platform,
            destination_id=destination_id,
            content=content,
        )
    except Exception as exc:
        permanent = _is_permanent_delivery_error(exc)
        async with async_session_factory() as db:
            delivery = await OutboundDeliveryRepository(db).mark_failure(
                delivery_id,
                error_code=_delivery_error_code(exc),
                max_attempts=settings.outbound_delivery_max_attempts,
                retry_delay=timedelta(seconds=_retry_delay_seconds(attempts)),
                permanent=permanent,
            )
            status = delivery.status
            await db.commit()
        return DeliveryOutcome(status=status, attempts=attempts, error=exc)

    async with async_session_factory() as db:
        await OutboundDeliveryRepository(db).mark_sent(
            delivery_id,
            platform_message_id=platform_message_id,
        )
        message = await MessageRepository(db).get_by_id(delivery.message_id)
        if message is not None:
            message.platform_message_id = platform_message_id
        await db.commit()

    logger.info(
        "outbound_delivery_sent",
        delivery_id=str(delivery_id),
        platform=platform,
        destination_id=destination_id,
    )
    return DeliveryOutcome(status=OUTBOUND_DELIVERY_SENT, attempts=attempts)


async def _send_to_platform(*, platform: str, destination_id: str, content: str) -> str:
    if platform != "telegram":
        raise ValueError(f"Unsupported outbound platform: {platform}")

    async with AsyncExitStack() as resources:
        adapter = TelegramAdapter()
        resources.push_async_callback(adapter.close)
        return await adapter.send_message(destination_id, content)


async def _list_recoverable_delivery_ids() -> list[uuid.UUID]:
    async with async_session_factory() as db:
        return await OutboundDeliveryRepository(db).list_dispatchable_ids(
            limit=settings.outbound_delivery_recovery_batch_size,
            processing_timeout=timedelta(
                seconds=settings.outbound_delivery_processing_timeout_seconds
            ),
        )


def _is_permanent_delivery_error(exc: Exception) -> bool:
    if isinstance(exc, PlatformTransportError):
        return False
    if isinstance(exc, PlatformHTTPError):
        status_code = exc.status_code
        if status_code is None:
            return False
        if status_code in {408, 409, 425, 429} or status_code >= 500:
            return False
        return 400 <= status_code < 500
    return isinstance(exc, (ValueError, LookupError))


def _delivery_error_code(exc: Exception) -> str:
    if isinstance(exc, PlatformHTTPError) and exc.status_code is not None:
        return f"PlatformHTTP{exc.status_code}"
    return type(exc).__name__


def _retry_delay_seconds(attempts: int) -> int:
    exponent = max(0, attempts - 1)
    return min(settings.outbound_delivery_retry_base_seconds * (2**exponent), 3600)
