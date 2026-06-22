from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging import get_logger
from app.infrastructure.db.repositories import (
    PlatformRepository, ClientRepository, ConversationRepository, MessageRepository,
)
from app.services.platforms.telegram.schemas import TelegramUpdate
from app.services.platforms.telegram.client import TelegramClient
from app.services.ai.gigachat_client import GigaChatClient

# 🧠 Импортируем наши новые сервисы
from app.services.ai.intent_classifier import IntentClassifier
from app.services.escalation.engine import EscalationEngine
from app.services.ai.prompt_builder import PromptBuilder

logger = get_logger(__name__)

class TelegramMessageService:
    def __init__(
        self, db: AsyncSession, platform_repo: PlatformRepository,
        client_repo: ClientRepository, conversation_repo: ConversationRepository,
        message_repo: MessageRepository, tg_client: TelegramClient, ai_client: GigaChatClient,
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
        await self.db.commit()

        # 🧠 Анализ намерения и эскалация
        intent = IntentClassifier.classify(text)
        should_escalate, reason = EscalationEngine.should_escalate(intent, text)

        if should_escalate:
            logger.warning("escalation_triggered", reason=reason, chat_id=chat_id)
            reply_text = "Отличный вопрос! Передам его Софии — она лично ответит в течение 15 минут 💛"
        else:
            # 🤖 Стандартный_flow — собираем умный контекст через PromptBuilder
            history_msgs = await self.message_repo.get_history(conversation.id, limit=10)
            ai_history = PromptBuilder.build_history(client, history_msgs)

            logger.info("calling_gigachat", chat_id=chat_id, intent=intent)
            reply_text = await self.ai_client.generate_response(ai_history)

        # 4. Сохраняем outbound
        await self.message_repo.create_message(
            conversation_id=conversation.id, direction="outbound", content=reply_text,
            is_escalation_trigger=should_escalate,
        )
        await self.db.commit()

        # 5. Отправляем в Telegram
        try:
            await self.tg_client.send_message(chat_id, reply_text)
        except Exception as e:
            logger.exception("failed_to_send_reply", chat_id=chat_id)