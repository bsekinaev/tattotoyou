"""
Telegram Webhook Endpoint.
Принимает апдейты, сохраняет их в БД и отправляет тестовый ответ.
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.clients.models import Client, Platform
from app.domain.conversations.models import Conversation, Message
from app.infrastructure.db.session import get_db_session
from app.services.platforms.telegram.client import TelegramClient
from app.services.platforms.telegram.schemas import TelegramUpdate

router = APIRouter()
logger = get_logger(__name__)
settings = get_settings()

# Инициализируем клиент (в проде лучше делать через Dependency Injection, но для MVP сойдёт)
tg_client = TelegramClient()


@router.post("/telegram")
async def telegram_webhook(
        request: Request,
        update: TelegramUpdate,  # FastAPI сам распарсит JSON в Pydantic-модель
        db: AsyncSession = Depends(get_db_session),
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
        logger.warning("invalid_webhook_secret", ip=request.client.host if request.client else "unknown")
        raise HTTPException(status_code=403, detail="Invalid secret token")

    # Игнорируем апдейты без текстовых сообщений (например, заход в чат)
    if not update.message or not update.message.text:
        return {"status": "ignored"}

    chat_id = update.message.chat.id
    text = update.message.text
    logger.info("telegram_update_received", chat_id=chat_id, text=text)

    # ============================================
    # 2. БИЗНЕС-ЛОГИКА: Сохранение в БД
    # ============================================

    # 2.1. Get-or-Create Платформы
    result = await db.execute(select(Platform).where(Platform.name == "telegram"))
    platform = result.scalar_one_or_none()
    if not platform:
        platform = Platform(name="telegram", webhook_secret=expected_secret)
        db.add(platform)
        await db.flush()  # flush нужен, чтобы получить platform.id до коммита

    # 2.2. Get-or-Create Клиента
    external_id = str(chat_id)
    result = await db.execute(
        select(Client).where(
            Client.platform_id == platform.id,
            Client.external_id == external_id
        )
    )
    client = result.scalar_one_or_none()
    if not client:
        client = Client(
            platform_id=platform.id,
            external_id=external_id,
            display_name=update.message.chat.first_name or "Unknown",
            username=update.message.chat.username,
        )
        db.add(client)
        await db.flush()

    # 2.3. Get-or-Create Активного Диалога
    # Считаем диалог активным, если было сообщение за последние 24 часа
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(Conversation).where(
            Conversation.client_id == client.id,
            Conversation.status == "active",
            Conversation.last_activity_at > day_ago,
        ).order_by(Conversation.last_activity_at.desc())
    )
    conversation = result.scalar_one_or_none()

    if not conversation:
        conversation = Conversation(client_id=client.id, status="active")
        db.add(conversation)
        await db.flush()

    # Обновляем время последней активности
    conversation.last_activity_at = datetime.now(timezone.utc)

    # 2.4. Сохраняем само Сообщение (append-only лог)
    inbound_msg = Message(
        conversation_id=conversation.id,
        direction="inbound",
        content=text,
        platform_message_id=str(update.message.message_id),
    )
    db.add(inbound_msg)

    # Коммит произойдёт автоматически благодаря нашему Dependency get_db_session

    # ============================================
    # 3. ОТВЕТ КЛИЕНТУ (Эхо для теста MVP)
    # ============================================
    reply_text = (
        f"Привет, {client.display_name}! 🎨\n"
        f"Я AI-ассистент студии <b>ТАТТУТУЮ</b>.\n\n"
        f"Твоё сообщение успешно сохранено в БД и передано на анализ (в следующих шагах).\n"
        f"Ты написал: <i>{text}</i>"
    )

    try:
        await tg_client.send_message(chat_id, reply_text)
    except Exception as e:
        logger.exception("failed_to_send_reply", chat_id=chat_id)

    # Telegram требует обязательного ответа 200 OK, иначе он будет спамить повторными запросами
    return {"status": "ok"}