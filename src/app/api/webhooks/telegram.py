import asyncio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.db.session import get_db_session, async_session_factory
from app.infrastructure.db.dependencies import (
    get_platform_repo, get_client_repo, get_conversation_repo, get_message_repo,
)
from app.services.platforms.telegram.client import get_telegram_client, TelegramClient
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.platforms.telegram.service import TelegramMessageService
from app.services.ai.gigachat_client import gigachat_client
from app.infrastructure.db.repositories import (
    PlatformRepository, ClientRepository, ConversationRepository, MessageRepository,
)

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()


async def _process_in_background(update_dict: dict, webhook_secret: str):
    """
    Фоновая задача. Она сама создает свою сессию БД,
    так как сессия из Depends(get_db_session) уже закрыта!
    """
    # Восстанавливаем Pydantic-модель из словаря
    update = TelegramUpdate(**update_dict)

    # Создаем НОВУЮ сессию и репозитории для фона
    async with async_session_factory() as db:
        service = TelegramMessageService(
            db=db,
            platform_repo=PlatformRepository(db),
            client_repo=ClientRepository(db),
            conversation_repo=ConversationRepository(db),
            message_repo=MessageRepository(db),
            tg_client=get_telegram_client(),
            ai_client=gigachat_client,
        )
        try:
            await service.process_update(update, webhook_secret)
        except Exception as e:
            logger.exception("background_task_failed", error=str(e))


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    update: TelegramUpdate,
    background_tasks: BackgroundTasks,  # 🆕 FastAPI BackgroundTasks
    db: AsyncSession = Depends(get_db_session),
    x_telegram_bot_api_secret_token: str | None = Header(None),
):
    # 1. Безопасность
    expected_secret = settings.telegram_webhook_secret.get_secret_value()
    if x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid secret token")

    logger.info("webhook_received", update_id=update.update_id)

    # 2. Делегируем в фон!
    # Передаем update как словарь (model_dump), чтобы он не зависел от жизненного цикла request
    background_tasks.add_task(
        _process_in_background,
        update.model_dump(by_alias=True),
        expected_secret
    )

    # 3. Мгновенно отвечаем Telegram 200 OK
    return {"status": "ok"}