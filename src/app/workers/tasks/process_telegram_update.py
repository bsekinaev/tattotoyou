import sys
import asyncio
from app.workers.celery_app import celery_app
from app.core.logging import get_logger
from app.infrastructure.db.session import async_session_factory
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.platforms.telegram.service import TelegramMessageService
from app.services.platforms.telegram.client import TelegramClient
from app.services.ai.gigachat_client import GigaChatClient
from app.services.ai.intent_classifier import IntentClassifier
from app.services.ai.fallback_responder import FallbackResponder  # 🆕
from app.infrastructure.db.repositories import (
    PlatformRepository, ClientRepository, ConversationRepository, MessageRepository,
)

logger = get_logger(__name__)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@celery_app.task(
    bind=True,
    name="process_telegram_update",
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 3, 'countdown': 5},
    retry_backoff=True,
)
def process_telegram_update_task(self, update_dict: dict, webhook_secret: str):
    logger.info("celery_task_started", update_id=update_dict.get("update_id"))
    try:
        asyncio.run(_process_async(update_dict, webhook_secret))
        logger.info("celery_task_completed", update_id=update_dict.get("update_id"))
    except Exception as exc:
        # КРИТИЧНО: Проверяем, не исчерпаны ли ретраи
        if self.request.retries >= self.max_retries:
            logger.error("task_permanently_failed_sending_fallback", error=str(exc))
            # Запускаем синхронный fallback, чтобы не бросать клиента
            asyncio.run(_send_fallback(update_dict))
        raise exc


async def _process_async(update_dict: dict, webhook_secret: str):
    # ... (твой текущий код без изменений) ...
    update = TelegramUpdate(**update_dict)
    tg_client = TelegramClient()
    ai_client = GigaChatClient()
    try:
        async with async_session_factory() as db:
            service = TelegramMessageService(...)
            await service.process_update(update, webhook_secret)
    finally:
        await tg_client.close()
        await ai_client.close()


# ФУНКЦИЯ ДЛЯ FALLBACK
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