from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.rate_limiter import RateLimiter

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
    # ==========================================
    # ШАГ 1: Безопасность (HMAC / Secret Token)
    # ==========================================
    expected_secret = settings.telegram_webhook_secret.get_secret_value()
    if x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    # Безопасно извлекаем chat_id для rate limiting
    chat_id = 0
    if update.message and update.message.chat:
        chat_id = update.message.chat.id

    # ==========================================
    # 🛡️ ШАГ 2: RATE LIMITING (Защита бюджета LLM)
    # ==========================================
    # Проверяем ЛИМИТ ДО того, как начнем тратить ресурсы (Redis, Celery, БД)
    if chat_id:
        rate_limiter = RateLimiter(
            redis_client=request.app.state.redis,
            max_requests=10,  # 10 сообщений
            window_seconds=60,  # в минуту
        )
        is_allowed, remaining = await rate_limiter.is_allowed(f"tg:{chat_id}")

        if not is_allowed:
            logger.warning("rate_limit_blocked", chat_id=chat_id)
            # Отвечаем Telegram 200 OK, чтобы он не ретраил webhook
            # Но задачу в Celery НЕ кидаем
            return {"status": "rate_limited"}

    # ==========================================
    # ШАГ 3: Дедупликация (Redis SETNX)
    # ==========================================
    redis = request.app.state.redis
    cache_key = f"tg_update:{update.update_id}"
    is_new = await redis.set(cache_key, "1", ex=86400, nx=True)
    if not is_new:
        logger.info("duplicate_update_ignored", update_id=update.update_id)
        return {"status": "ignored"}

    # ==========================================
    # 🚀 ШАГ 4: Отправляем в очередь Celery
    # ==========================================
    process_telegram_update_task.delay(update.model_dump(by_alias=True), expected_secret)

    logger.info("task_sent_to_celery", update_id=update.update_id)

    # Мгновенно отвечаем Telegram 200 OK
    return {"status": "ok"}
