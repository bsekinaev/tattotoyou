from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging import get_logger
from app.infrastructure.db.repositories import (
    PlatformRepository, ClientRepository, ConversationRepository, MessageRepository,
)
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.platforms.telegram.client import TelegramClient
from app.services.ai.gigachat_client import GigaChatClient

logger = get_logger(__name__)

# Системный промт для Лики (ассистента ТАТТУТУЮ)
SYSTEM_PROMPT = (
    "Ты — Лика, администратор тату-студии ТАТТУТУЮ (мастер София). "
    "Отвечай дружелюбно, кратко (2-3 предложения). Используй эмодзи 🎨✨. "
    "Если спрашивают цену, говори, что точный расчет только после эскиза, но минималка от 3000р. "
    "Твоя цель — записать клиента на консультацию."
)

class TelegramMessageService:
    def __init__(
        self,
        db: AsyncSession,
        platform_repo: PlatformRepository,
        client_repo: ClientRepository,
        conversation_repo: ConversationRepository,
        message_repo: MessageRepository,
        tg_client: TelegramClient,
        ai_client: GigaChatClient,
    ):
        self.db = db
        self.platform_repo = platform_repo
        self.client_repo = client_repo
        self.conversation_repo = conversation_repo
        self.message_repo = message_repo
        self.tg_client = tg_client
        self.ai_client = ai_client

    async def process_update(self, update: TelegramUpdate, webhook_secret: str) -> None:
        if not update.message or not update.message.text:
            return

        chat_id = update.message.chat.id
        text = update.message.text

        # 1. Сохраняем в БД
        platform = await self.platform_repo.get_or_create(name="telegram", webhook_secret=webhook_secret)
        client = await self.client_repo.get_or_create(
            platform_id=platform.id, external_id=str(chat_id),
            display_name=update.message.chat.first_name or "Гость",
            username=update.message.chat.username,
        )
        conversation = await self.conversation_repo.get_or_create_active(client_id=client.id)
        await self.conversation_repo.update_activity(conversation)

        await self.message_repo.create_message(
            conversation_id=conversation.id, direction="inbound",
            content=text, platform_message_id=str(update.message.message_id),
        )
        await self.db.commit() # Фиксируем inbound сообщение

        # 2. Формируем историю для AI
        history_msgs = await self.message_repo.get_history(conversation.id, limit=10)
        ai_history = [{"role": "system", "content": SYSTEM_PROMPT}]
        for msg in history_msgs:
            role = "user" if msg.direction == "inbound" else "assistant"
            ai_history.append({"role": role, "content": msg.content})

        # 3. Генерируем ответ через GigaChat
        logger.info("calling_gigachat", chat_id=chat_id)
        reply_text = await self.ai_client.generate_response(ai_history)

        # 4. Сохраняем outbound сообщение
        await self.message_repo.create_message(
            conversation_id=conversation.id, direction="outbound", content=reply_text,
        )
        await self.db.commit()

        # 5. Отправляем в Telegram
        try:
            await self.tg_client.send_message(chat_id, reply_text)
        except Exception as e:
            logger.exception("failed_to_send_reply", chat_id=chat_id)