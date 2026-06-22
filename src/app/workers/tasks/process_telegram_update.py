import asyncio
import sys

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.platforms.telegram_adapter import TelegramAdapter
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    MessageRepository,
    PlatformRepository,
)
from app.infrastructure.db.session import async_session_factory
from app.services.ai.fallback_responder import FallbackResponder
from app.services.ai.gigachat_client import GigaChatClient
from app.services.ai.intent_classifier import IntentClassifier
from app.services.conversation_service import ConversationService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
settings = get_settings()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@celery_app.task(
    bind=True,
    name="process_telegram_update",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 5},
    retry_backoff=True,
)
def process_telegram_update_task(self, update_dict: dict, webhook_secret: str):
    logger.info("celery_task_started", update_id=update_dict.get("update_id"))
    try:
        asyncio.run(_process_async(update_dict, webhook_secret))
        logger.info("celery_task_completed", update_id=update_dict.get("update_id"))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error("task_permanently_failed_sending_fallback", error=str(exc))
            asyncio.run(_send_fallback(update_dict))
        raise exc


async def _process_async(update_dict: dict, webhook_secret: str):
    # Создаём адаптер и парсим сообщение
    telegram_adapter = TelegramAdapter()
    message = await telegram_adapter.parse_message(update_dict)

    if not message:
        logger.info("skipping_non_message_update")
        return

    # Создаём Redis-клиент для distributed token caching
    redis_client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
    )

    # Создаём AI-клиент с Redis cache
    ai_client = GigaChatClient(redis_client=redis_client)

    try:
        async with async_session_factory() as db:
            # ConversationService работает с абстрактным PlatformAdapter
            service = ConversationService(
                db=db,
                platform_repo=PlatformRepository(db),
                client_repo=ClientRepository(db),
                conversation_repo=ConversationRepository(db),
                message_repo=MessageRepository(db),
                platform_adapter=telegram_adapter,  # ← Platform Adapter!
                ai_client=ai_client,
            )
            await service.process_message(message)
    finally:
        await telegram_adapter.close()
        await ai_client.close()
        await redis_client.close()


async def _send_fallback(update_dict: dict):
    """Отправляет шаблонный ответ, если LLM и БД окончательно упали."""
    try:
        telegram_adapter = TelegramAdapter()
        message = await telegram_adapter.parse_message(update_dict)

        if not message or not message.text:
            return

        intent = IntentClassifier.classify(message.text)
        fallback_text = FallbackResponder.get_response(intent)

        try:
            await telegram_adapter.send_message(message.chat_id, fallback_text)
            logger.info("fallback_message_sent", chat_id=message.chat_id)
        finally:
            await telegram_adapter.close()
    except Exception as e:
        logger.exception("fallback_send_failed", error=str(e))