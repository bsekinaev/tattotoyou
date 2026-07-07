from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.platforms.telegram_adapter import TelegramAdapter
from app.domain.incoming.models import INCOMING_EVENT_FAILED, INCOMING_EVENT_RETRYING
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    IncomingEventRepository,
    MessageRepository,
    OutboundDeliveryRepository,
    PlatformRepository,
)
from app.infrastructure.db.worker_session import worker_session_scope as async_session_factory
from app.services.ai.fallback_responder import FallbackResponder
from app.services.ai.gigachat_client import GigaChatClient
from app.services.ai.intent_classifier import IntentClassifier
from app.services.conversation_service import ConversationService
from app.services.notifications.admin_notifier import AdminNotifier
from app.workers.celery_app import celery_app
from app.workers.tasks.deliver_outbound_message import deliver_outbound_message_task

logger = get_logger(__name__)
settings = get_settings()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@dataclass(slots=True)
class EventProcessingOutcome:
    status: str
    payload: dict[str, Any] | None = None
    attempts: int = 0
    error: Exception | None = None


@celery_app.task(
    bind=True,
    name="process_telegram_update",
    max_retries=settings.incoming_event_max_attempts,
)
def process_telegram_update_task(self, event_reference: str | dict[str, Any]):
    """Обработать долговечное Inbox-событие.

    Словарь поддерживается временно для уже поставленных задач старой версии.
    Новые webhook'и передают только UUID записи ``incoming_events``.
    """
    try:
        if isinstance(event_reference, dict):
            event_id = asyncio.run(_persist_legacy_update(event_reference))
        else:
            event_id = uuid.UUID(str(event_reference))

        logger.info("incoming_event_task_started", event_id=str(event_id))
        outcome = asyncio.run(_process_incoming_event(event_id))
    except Exception as exc:
        logger.exception(
            "incoming_event_task_infrastructure_failed",
            error_type=type(exc).__name__,
        )
        raise self.retry(
            exc=exc,
            countdown=_retry_delay_seconds(self.request.retries + 1),
        ) from exc

    if outcome.status == INCOMING_EVENT_RETRYING:
        logger.warning(
            "incoming_event_retry_scheduled",
            event_id=str(event_id),
            attempts=outcome.attempts,
            error_type=type(outcome.error).__name__ if outcome.error else None,
        )
        raise self.retry(
            exc=outcome.error,
            countdown=_retry_delay_seconds(outcome.attempts),
        )

    if outcome.status == INCOMING_EVENT_FAILED:
        logger.error(
            "incoming_event_permanently_failed",
            event_id=str(event_id),
            attempts=outcome.attempts,
        )
        if outcome.payload is not None:
            asyncio.run(
                _send_fallback(
                    outcome.payload,
                    causation_event_id=event_id,
                )
            )
        return {"status": INCOMING_EVENT_FAILED, "event_id": str(event_id)}

    logger.info(
        "incoming_event_task_completed",
        event_id=str(event_id),
        status=outcome.status,
    )
    return {"status": outcome.status, "event_id": str(event_id)}


@celery_app.task(name="recover_incoming_events")
def recover_incoming_events_task() -> dict[str, int]:
    """Повторно поставить готовые и зависшие Inbox-события в очередь."""
    event_ids = asyncio.run(_list_recoverable_event_ids())
    dispatched = 0

    for event_id in event_ids:
        try:
            process_telegram_update_task.delay(str(event_id))
            dispatched += 1
        except Exception as exc:
            logger.exception(
                "incoming_event_recovery_dispatch_failed",
                event_id=str(event_id),
                error_type=type(exc).__name__,
            )

    logger.info(
        "incoming_event_recovery_completed",
        found=len(event_ids),
        dispatched=dispatched,
    )
    return {"found": len(event_ids), "dispatched": dispatched}


async def _persist_legacy_update(update_dict: dict[str, Any]) -> uuid.UUID:
    """Сохранить задачу старого формата в Inbox перед обработкой."""
    update_id = update_dict.get("update_id")
    if update_id is None:
        raise ValueError("Legacy Telegram update does not contain update_id")

    async with async_session_factory() as db:
        event, _created = await IncomingEventRepository(db).get_or_create(
            platform="telegram",
            external_event_id=str(update_id),
            payload=update_dict,
        )
        await db.commit()
        return event.id


async def _process_incoming_event(event_id: uuid.UUID) -> EventProcessingOutcome:
    """Захватить событие, выполнить бизнес-обработку и обновить Inbox."""
    processing_timeout = timedelta(seconds=settings.incoming_event_processing_timeout_seconds)

    async with async_session_factory() as db:
        event = await IncomingEventRepository(db).claim(
            event_id,
            processing_timeout=processing_timeout,
        )
        if event is None:
            await db.rollback()
            return EventProcessingOutcome(status="ignored")

        payload = dict(event.payload)
        attempts = event.attempts
        await db.commit()

    try:
        await _process_async(payload, causation_event_id=event_id)
    except Exception as exc:
        retry_delay = timedelta(seconds=_retry_delay_seconds(attempts))
        async with async_session_factory() as db:
            event = await IncomingEventRepository(db).mark_failure(
                event_id,
                error_code=type(exc).__name__,
                max_attempts=settings.incoming_event_max_attempts,
                retry_delay=retry_delay,
            )
            status = event.status
            await db.commit()

        return EventProcessingOutcome(
            status=status,
            payload=payload,
            attempts=attempts,
            error=exc,
        )

    async with async_session_factory() as db:
        await IncomingEventRepository(db).mark_processed(event_id)
        await db.commit()

    return EventProcessingOutcome(
        status="processed",
        payload=payload,
        attempts=attempts,
    )


async def _list_recoverable_event_ids() -> list[uuid.UUID]:
    async with async_session_factory() as db:
        return await IncomingEventRepository(db).list_dispatchable_ids(
            limit=settings.incoming_event_recovery_batch_size,
            processing_timeout=timedelta(
                seconds=settings.incoming_event_processing_timeout_seconds
            ),
        )


def _retry_delay_seconds(attempts: int) -> int:
    exponent = max(0, attempts - 1)
    return min(settings.incoming_event_retry_base_seconds * (2**exponent), 3600)


async def _process_async(
    update_dict: dict[str, Any],
    causation_event_id: uuid.UUID | None = None,
) -> None:
    """Обработать Telegram update и закрыть все созданные async-ресурсы."""
    async with AsyncExitStack() as resources:
        telegram_adapter = TelegramAdapter()
        resources.push_async_callback(telegram_adapter.close)

        message = await telegram_adapter.parse_message(update_dict)
        if not message:
            logger.info("skipping_non_message_update")
            return

        redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        resources.push_async_callback(redis_client.aclose)

        ai_client = GigaChatClient(redis_client=redis_client)
        resources.push_async_callback(ai_client.close)

        async with async_session_factory() as db:
            service = ConversationService(
                db=db,
                platform_repo=PlatformRepository(db),
                client_repo=ClientRepository(db),
                conversation_repo=ConversationRepository(db),
                message_repo=MessageRepository(db),
                platform_adapter=telegram_adapter,
                ai_client=ai_client,
            )
            await service.process_message(
                message,
                causation_event_id=causation_event_id,
            )


async def _send_fallback(
    update_dict: dict[str, Any],
    *,
    causation_event_id: uuid.UUID | None = None,
) -> None:
    """Сохранить финальный fallback и уведомление Соне через Outbox."""
    delivery_ids: list[uuid.UUID] = []
    try:
        async with AsyncExitStack() as resources:
            telegram_adapter = TelegramAdapter()
            resources.push_async_callback(telegram_adapter.close)

            message = await telegram_adapter.parse_message(update_dict)
            if not message or not message.text:
                return

            async with async_session_factory() as db:
                message_repo = MessageRepository(db)
                outbox_repo = OutboundDeliveryRepository(db)

                if causation_event_id is not None:
                    existing_outbound = await message_repo.get_by_causation(
                        causation_event_id,
                        "outbound",
                    )
                    if existing_outbound is not None:
                        delivery = await outbox_repo.get_by_message_id(existing_outbound.id)
                        if delivery is None and existing_outbound.platform_message_id is None:
                            delivery, _created = await outbox_repo.create_for_message(
                                message_id=existing_outbound.id,
                                platform=message.platform,
                                destination_id=message.chat_id,
                            )
                        await db.commit()
                        if delivery is not None:
                            delivery_ids.append(delivery.id)
                    else:
                        delivery_ids.extend(
                            await _persist_fallback_effect(
                                db=db,
                                message=message,
                                causation_event_id=causation_event_id,
                            )
                        )
                else:
                    delivery_ids.extend(
                        await _persist_fallback_effect(
                            db=db,
                            message=message,
                            causation_event_id=None,
                        )
                    )

        for delivery_id in dict.fromkeys(delivery_ids):
            deliver_outbound_message_task.delay(str(delivery_id))
            logger.info(
                "fallback_outbox_dispatched",
                delivery_id=str(delivery_id),
            )
    except Exception as exc:
        logger.exception("fallback_outbox_failed", error_type=type(exc).__name__)


async def _persist_fallback_effect(
    *,
    db: Any,
    message: Any,
    causation_event_id: uuid.UUID | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Атомарно сохранить клиентский fallback и уведомление администратора."""
    platform_repo = PlatformRepository(db)
    client_repo = ClientRepository(db)
    conversation_repo = ConversationRepository(db)
    message_repo = MessageRepository(db)
    outbox_repo = OutboundDeliveryRepository(db)

    platform = await platform_repo.get_or_create(name=message.platform, webhook_secret="")
    client = await client_repo.get_or_create(
        platform_id=platform.id,
        external_id=message.user.external_id,
        display_name=message.user.display_name,
        username=message.user.username,
    )
    conversation = await conversation_repo.get_or_create_active(client_id=client.id)
    await conversation_repo.update_activity(conversation)

    if causation_event_id is None:
        await message_repo.create_message(
            conversation_id=conversation.id,
            direction="inbound",
            sender_type="client",
            content=message.text,
            platform_message_id=message.message_id,
        )
    else:
        await message_repo.create_message_once(
            conversation_id=conversation.id,
            direction="inbound",
            sender_type="client",
            content=message.text,
            platform_message_id=message.message_id,
            causation_event_id=causation_event_id,
        )

    intent = IntentClassifier.classify_detailed(message.text).intent
    fallback_text = FallbackResponder.get_response(intent)
    if causation_event_id is None:
        outbound = await message_repo.create_message(
            conversation_id=conversation.id,
            direction="outbound",
            sender_type="bot",
            content=fallback_text,
            is_escalation_trigger=True,
        )
    else:
        outbound, _created = await message_repo.create_message_once(
            conversation_id=conversation.id,
            direction="outbound",
            sender_type="bot",
            content=fallback_text,
            causation_event_id=causation_event_id,
            is_escalation_trigger=True,
        )

    delivery, _created = await outbox_repo.create_for_message(
        message_id=outbound.id,
        platform=message.platform,
        destination_id=message.chat_id,
    )
    notification, _notification_created = await outbox_repo.create_notification(
        platform="telegram",
        destination_id=str(settings.telegram_admin_chat_id),
        payload_text=AdminNotifier.build_outbox_message(
            client_name=client.display_name or "Гость",
            client_username=client.username,
            reason="incoming_event_processing_failed",
            last_message=message.text,
            chat_id=message.chat_id,
        ),
        deduplication_key=_terminal_fallback_deduplication_key(
            conversation_id=conversation.id,
            outbound_message_id=outbound.id,
            causation_event_id=causation_event_id,
        ),
    )
    await conversation_repo.escalate(conversation)
    await db.commit()
    return delivery.id, notification.id


def _terminal_fallback_deduplication_key(
    *,
    conversation_id: uuid.UUID,
    outbound_message_id: int,
    causation_event_id: uuid.UUID | None,
) -> str:
    if causation_event_id is not None:
        return f"terminal-fallback:event:{causation_event_id}"
    return f"terminal-fallback:conversation:{conversation_id}:message:{outbound_message_id}"
