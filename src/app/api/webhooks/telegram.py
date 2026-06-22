# src/app/api/webhooks/telegram.py
from fastapi import APIRouter, Header, HTTPException, Request
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.platforms.telegram.schemas import TelegramUpdate

# 🚀 Импортируем Celery задачу
from app.workers.tasks.process_telegram_update import process_telegram_update_task

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


@router.post("/telegram")
async def telegram_webhook(
        request: Request,
        update: TelegramUpdate,
        x_telegram_bot_api_secret_token: str | None = Header(None),
):
    # 1. Безопасность (HMAC / Secret Token)
    expected_secret = settings.telegram_webhook_secret.get_secret_value()
    if x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    # 2. 🛡️ ДЕДУПЛИКАЦИЯ (Redis SETNX)
    redis = request.app.state.redis
    cache_key = f"tg_update:{update.update_id}"
    is_new = await redis.set(cache_key, "1", ex=86400, nx=True)
    if not is_new:
        logger.info("duplicate_update_ignored", update_id=update.update_id)
        return {"status": "ignored"}

    # 3. 🚀 Отправляем в очередь Celery (FastAPI мгновенно освобождается)
    process_telegram_update_task.delay(
        update.model_dump(by_alias=True),
        expected_secret
    )

    logger.info("task_sent_to_celery", update_id=update.update_id)

    # 4. Мгновенно отвечаем Telegram 200 OK
    return {"status": "ok"}