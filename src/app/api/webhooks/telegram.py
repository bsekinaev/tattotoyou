from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import secrets_match
from app.domain.incoming.models import DISPATCHABLE_INCOMING_EVENT_STATUSES
from app.infrastructure.db.repositories import IncomingEventRepository
from app.infrastructure.db.session import async_session_factory
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.rate_limiter import RateLimiter
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
    """Принять Telegram update и долговечно зафиксировать его до Celery."""
    expected_secret = settings.telegram_webhook_secret.get_secret_value()
    if not secrets_match(x_telegram_bot_api_secret_token, expected_secret):
        raise HTTPException(status_code=403, detail="Invalid secret token")

    chat_id = 0
    if update.message and update.message.chat:
        chat_id = update.message.chat.id

    # Ограничиваем расходы до записи события и постановки в очередь.
    if chat_id:
        rate_limiter = RateLimiter(
            redis_client=request.app.state.redis,
            max_requests=10,
            window_seconds=60,
        )
        is_allowed, _remaining = await rate_limiter.is_allowed(f"tg:{chat_id}")
        if not is_allowed:
            logger.warning("rate_limit_blocked", chat_id=chat_id)
            return {"status": "rate_limited"}

    payload = update.model_dump(by_alias=True, mode="json")
    async with async_session_factory() as db:
        event, created = await IncomingEventRepository(db).get_or_create(
            platform="telegram",
            external_event_id=str(update.update_id),
            payload=payload,
        )
        await db.commit()

    # PostgreSQL уже является источником истины. Ошибка брокера не теряет
    # событие: pending/retrying запись подберёт recovery-задача.
    dispatched = False
    if event.status in DISPATCHABLE_INCOMING_EVENT_STATUSES:
        try:
            process_telegram_update_task.delay(str(event.id))
            dispatched = True
        except Exception as exc:
            logger.exception(
                "incoming_event_dispatch_failed",
                event_id=str(event.id),
                update_id=update.update_id,
                error_type=type(exc).__name__,
            )

    logger.info(
        "incoming_event_accepted",
        event_id=str(event.id),
        update_id=update.update_id,
        created=created,
        status=event.status,
        dispatched=dispatched,
    )
    return {
        "status": "accepted" if created else "duplicate",
        "dispatched": dispatched,
    }
