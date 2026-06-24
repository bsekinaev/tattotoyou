"""
Conversation Service — ядро бизнес-логики.

Работает с PlatformMessage и PlatformAdapter, не зная деталей конкретной платформы.
Это позволяет использовать одну и ту же логику для Telegram, VK, Instagram.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.platforms.base import PlatformAdapter, PlatformMessage
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    MessageRepository,
    PlatformRepository,
)
from app.services.ai.gigachat_client import GigaChatClient
from app.services.ai.intent_classifier import IntentClassifier
from app.services.ai.prompt_builder import PromptBuilder
from app.services.escalation.engine import EscalationEngine
from app.workers.tasks.send_admin_notification import send_admin_notification_task

logger = get_logger(__name__)


class ConversationService:
    """
    Platform-agnostic сервис обработки диалогов.

    Работает с абстрактными PlatformMessage и PlatformAdapter.
    Не знает, какая платформа (Telegram/VK/Instagram) используется.
    """

    def __init__(
        self,
        db: AsyncSession,
        platform_repo: PlatformRepository,
        client_repo: ClientRepository,
        conversation_repo: ConversationRepository,
        message_repo: MessageRepository,
        platform_adapter: PlatformAdapter,
        ai_client: GigaChatClient,
    ):
        self.db = db
        self.platform_repo = platform_repo
        self.client_repo = client_repo
        self.conversation_repo = conversation_repo
        self.message_repo = message_repo
        self.platform_adapter = platform_adapter
        self.ai_client = ai_client

    async def process_message(self, message: PlatformMessage) -> None:
        """
        Обработать входящее сообщение от любой платформы.

        Flow:
        1. Сохранить сообщение в БД
        2. Классифицировать intent
        3. Проверить escalation rules
        4. Сгенерировать ответ (LLM или escalation)
        5. Отправить ответ через PlatformAdapter
        """
        if not message.text:
            return

        logger.info(
            "processing_message",
            platform=message.platform,
            chat_id=message.chat_id,
            user_id=message.user.external_id,
        )

        # 1. Persistence: сохраняем в БД
        platform = await self.platform_repo.get_or_create(
            name=message.platform,
            webhook_secret="",  # webhook_secret хранится в adapter
        )

        client = await self.client_repo.get_or_create(
            platform_id=platform.id,
            external_id=message.user.external_id,
            display_name=message.user.display_name or "Гость",
            username=message.user.username,
        )

        conversation = await self.conversation_repo.get_or_create_active(client_id=client.id)
        await self.conversation_repo.update_activity(conversation)

        # Сохраняем inbound message
        await self.message_repo.create_message(
            conversation_id=conversation.id,
            direction="inbound",
            content=message.text,
            platform_message_id=message.message_id,
        )
        await self.db.commit()

        # 2. Intent Classification
        intent = IntentClassifier.classify(message.text)
        should_escalate, reason = EscalationEngine.should_escalate(intent, message.text)

        # 3. Routing: Escalation vs AI
        if should_escalate:
            logger.warning(
                "escalation_triggered",
                reason=reason,
                chat_id=message.chat_id,
                intent=intent,
            )
            reply_text = (
                "Отличный вопрос! Передам его Софии — она лично ответит в течение 15 минут 💛"
            )

            # Уведомляем админа через Celery (async)
            send_admin_notification_task.delay(
                client_name=client.display_name or "Гость",
                client_username=client.username,
                reason=reason,
                last_message=message.text,
                chat_id=int(message.chat_id),  # Celery требует serializable types
            )
        else:
            # AI Flow: собираем контекст и запрашиваем LLM
            history_msgs = await self.message_repo.get_history(
                conversation.id,
                limit=10,
            )
            ai_history = PromptBuilder.build_history(client, history_msgs)

            logger.info(
                "calling_gigachat",
                chat_id=message.chat_id,
                intent=intent,
                history_length=len(ai_history),
            )
            reply_text = await self.ai_client.generate_response(ai_history)

        # 4. Сохраняем outbound message
        await self.message_repo.create_message(
            conversation_id=conversation.id,
            direction="outbound",
            content=reply_text,
            is_escalation_trigger=should_escalate,
        )
        await self.db.commit()

        # 5. Отправляем ответ через PlatformAdapter
        try:
            await self.platform_adapter.send_message(message.chat_id, reply_text)
            logger.info("reply_sent", chat_id=message.chat_id, platform=message.platform)
        except Exception as e:
            # Логируем, но не падаем — сообщение уже сохранено в БД
            logger.exception(
                "failed_to_send_reply",
                chat_id=message.chat_id,
                platform=message.platform,
                error_type=type(e).__name__,
            )
