# src/app/workers/tasks/process_telegram_update.py
import asyncio
import sys

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger
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
from app.services.platforms.telegram.client import TelegramClient
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.platforms.telegram.service import TelegramMessageService
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
    update = TelegramUpdate(**update_dict)

    # 🛡️ Создаём СВЕЖИЕ клиенты для текущего Event Loop'а
    tg_client = TelegramClient()

    # 🚀 НОВОЕ: Создаём Redis-клиент для distributed token caching
    # decode_responses=True — чтобы строки возвращались как str, а не bytes
    redis_client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
    )

    # Передаём Redis-клиент в GigaChatClient для кэширования токенов
    ai_client = GigaChatClient(redis_client=redis_client)

    try:
        async with async_session_factory() as db:
            service = TelegramMessageService(
                db=db,
                platform_repo=PlatformRepository(db),
                client_repo=ClientRepository(db),
                conversation_repo=ConversationRepository(db),
                message_repo=MessageRepository(db),
                tg_client=tg_client,
                ai_client=ai_client,
            )
            await service.process_update(update, webhook_secret)
    finally:
        # 🛡️ Закрываем ВСЕ клиенты ДО закрытия Event Loop'а
        await tg_client.close()
        await ai_client.close()
        await redis_client.close()


async def _send_fallback(update_dict: dict):
    """Отправляет шаблонный ответ, если LLM и БД окончательно упали."""
    try:
        update = TelegramUpdate(**update_dict)
        if not update.message or not update.message.text:
            return

        chat_id = update.message.chat.id
        text = update.message.text

        intent = IntentClassifier.classify(text)
        fallback_text = FallbackResponder.get_response(intent)

        tg_client = TelegramClient()
        try:
            await tg_client.send_message(chat_id, fallback_text)
            logger.info("fallback_message_sent", chat_id=chat_id)
        finally:
            await tg_client.close()
    except Exception as e:
        logger.exception("fallback_send_failed", error=str(e))
