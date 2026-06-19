"""
Сервис для обработки входящих сообщений из Telegram.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.infrastructure.db.repositories import (
    PlatformRepository,
    ClientRepository,
    ConversationRepository,
    MessageRepository,
)
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.platforms.telegram.client import TelegramClient

logger = get_logger(__name__)


class TelegramMessageService:
    def __init__(
            self,
            db: AsyncSession,
            platform_repo: PlatformRepository,
            client_repo: ClientRepository,
            conversation_repo: ConversationRepository,
            message_repo: MessageRepository,
            tg_client: TelegramClient,
    ):
        self.db = db
        self.platform_repo = platform_repo
        self.client_repo = client_repo
        self.conversation_repo = conversation_repo
        self.message_repo = message_repo
        self.tg_client = tg_client

    async def process_update(self, update: TelegramUpdate, webhook_secret: str) -> None:
        """
        Главный метод обработки апдейта.
        """
        if not update.message or not update.message.text:
            logger.info("ignored_non_text_update", update_id=update.update_id)
            return

        chat_id = update.message.chat.id
        text = update.message.text

        # 1. Get-or-Create Платформы
        platform = await self.platform_repo.get_or_create(
            name="telegram",
            webhook_secret=webhook_secret
        )

        # 2. Get-or-Create Клиента
        client = await self.client_repo.get_or_create(
            platform_id=platform.id,
            external_id=str(chat_id),
            display_name=update.message.chat.first_name or "Unknown",
            username=update.message.chat.username,
        )

        # 3. Get-or-Create Активного Диалога
        conversation = await self.conversation_repo.get_or_create_active(client_id=client.id)
        await self.conversation_repo.update_activity(conversation)

        # 4. Сохраняем Сообщение (append-only лог)
        await self.message_repo.create_message(
            conversation_id=conversation.id,
            direction="inbound",
            content=text,
            platform_message_id=str(update.message.message_id),
        )

        # 5. Отправляем ответ (Эхо для MVP)
        reply_text = (
            f"Привет, {client.display_name}! 🎨\n"
            f"Я AI-ассистент студии <b>ТАТТУТУЮ</b>.\n\n"
            f"Твоё сообщение успешно сохранено в БД.\n"
            f"Ты написал: <i>{text}</i>"
        )

        try:
            await self.tg_client.send_message(chat_id, reply_text)
        except Exception as e:
            logger.exception("failed_to_send_reply", chat_id=chat_id, error=str(e))