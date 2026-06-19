"""
Telegram Webhook Endpoint.
Тонкий контроллер: только проверка безопасности и делегирование работы сервису.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.db.session import get_db_session
from app.infrastructure.db.dependencies import (
    get_platform_repo,
    get_client_repo,
    get_conversation_repo,
    get_message_repo,
)
from app.services.platforms.telegram.client import get_telegram_client, TelegramClient
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.platforms.telegram.service import TelegramMessageService

# Импорты репозиториев для type hints (опционально, но полезно для IDE)
from app.infrastructure.db.repositories import (
    PlatformRepository,
    ClientRepository,
    ConversationRepository,
    MessageRepository,
)

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    update: TelegramUpdate,
    # Инфраструктурные зависимости
    db: AsyncSession = Depends(get_db_session),
    platform_repo: PlatformRepository = Depends(get_platform_repo),
    client_repo: ClientRepository = Depends(get_client_repo),
    conversation_repo: ConversationRepository = Depends(get_conversation_repo),
    message_repo: MessageRepository = Depends(get_message_repo),
    tg_client: TelegramClient = Depends(get_telegram_client),
    # Безопасность
    x_telegram_bot_api_secret_token: str | None = Header(None),
):
    """
    Эндпоинт для приёма webhook'ов от Telegram.
    """
    # ============================================
    # 1. БЕЗОПАСНОСТЬ: Проверка секретного токена
    # ============================================
    expected_secret = settings.telegram_webhook_secret.get_secret_value()
    if x_telegram_bot_api_secret_token != expected_secret:
        logger.warning(
            "invalid_webhook_secret",
            ip=request.client.host if request.client else "unknown"
        )
        raise HTTPException(status_code=403, detail="Invalid secret token")

    logger.info(
        "telegram_update_received",
        update_id=update.update_id,
        chat_id=update.message.chat.id if update.message else None
    )

    # ============================================
    # 2. БИЗНЕС-ЛОГИКА: Делегируем сервису
    # ============================================
    service = TelegramMessageService(
        db=db,
        platform_repo=platform_repo,
        client_repo=client_repo,
        conversation_repo=conversation_repo,
        message_repo=message_repo,
        tg_client=tg_client,
    )

    await service.process_update(update, expected_secret)

    # Telegram требует обязательного ответа 200 OK
    return {"status": "ok"}