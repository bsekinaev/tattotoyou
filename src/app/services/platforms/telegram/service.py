"""
Сервис обработки входящих Telegram-сообщений.

Отвечает за оркестрацию всего pipeline:
1. Сохранение сообщения в БД
2. Классификация намерения (Intent Classification)
3. Проверка правил эскалации (Escalation Engine)
4. Формирование контекста для LLM (Prompt Builder)
5. Генерация ответа (GigaChat) или эскалация на мастера
6. Отправка уведомления Софии через Celery (при эскалации)
7. Отправка ответа клиенту в Telegram
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    MessageRepository,
    PlatformRepository,
)
from app.services.ai.gigachat_client import GigaChatClient

# 🧠 Бизнес-логика (Итерация 1)
from app.services.ai.intent_classifier import IntentClassifier
from app.services.ai.prompt_builder import PromptBuilder
from app.services.escalation.engine import EscalationEngine
from app.services.platforms.telegram.client import TelegramClient
from app.services.platforms.telegram.schemas import TelegramUpdate

# 📨 Асинхронные уведомления через Celery
from app.workers.tasks.send_admin_notification import send_admin_notification_task

logger = get_logger(__name__)


class TelegramMessageService:
    """
    Оркестратор обработки входящего Telegram-сообщения.

    Следует принципу Single Responsibility: сам не ходит в БД и не вызывает API,
    а делегирует работу специализированным сервисам (репозиториям, клиентам, классификаторам).
    """

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

    async def process_update(
        self,
        update: TelegramUpdate,
        webhook_secret: str,
    ) -> None:
        """
        Главный метод: полный lifecycle обработки одного inbound-сообщения.

        Args:
            update: Валидированный Telegram Update от webhook
            webhook_secret: Секрет для верификации платформы
        """
        # Фильтруем системные обновления (без текста)
        if not update.message or not update.message.text:
            return

        chat_id = update.message.chat.id
        text = update.message.text

        # ========================================
        # ШАГ 1: Persistence (сохраняем в БД)
        # ========================================
        platform = await self.platform_repo.get_or_create(
            name="telegram",
            webhook_secret=webhook_secret,
        )

        client = await self.client_repo.get_or_create(
            platform_id=platform.id,
            external_id=str(chat_id),
            display_name=update.message.chat.first_name or "Гость",
            username=update.message.chat.username,
        )

        conversation = await self.conversation_repo.get_or_create_active(
            client_id=client.id,
        )
        await self.conversation_repo.update_activity(conversation)

        # Сохраняем inbound сообщение
        await self.message_repo.create_message(
            conversation_id=conversation.id,
            direction="inbound",
            content=text,
            platform_message_id=str(update.message.message_id),
        )
        await self.db.commit()

        # ========================================
        # ШАГ 2: 🧠 Анализ намерения (Intent Classification)
        # ========================================
        # Быстрая keyword-based классификация (не тратит токены LLM)
        intent = IntentClassifier.classify(text)
        should_escalate, reason = EscalationEngine.should_escalate(intent, text)

        # ========================================
        # ШАГ 3: Маршрутизация (Эскалация vs AI)
        # ========================================
        if should_escalate:
            # 🚨 ЭСКАЛАЦИЯ: ИИ не отвечает, передаём человеку
            logger.warning(
                "escalation_triggered",
                reason=reason,
                chat_id=chat_id,
                intent=intent,
            )
            reply_text = (
                "Отличный вопрос! Передам его Софии — она лично ответит в течение 15 минут 💛"
            )

            # 📨 Асинхронно уведомляем Софию через Celery
            # (не блокируем основной pipeline)
            send_admin_notification_task.delay(
                client_name=client.display_name or "Гость",
                client_username=client.username,
                reason=reason,
                last_message=text,
                chat_id=chat_id,
            )
        else:
            # 🤖 AI FLOW: собираем контекст и запрашиваем LLM
            history_msgs = await self.message_repo.get_history(
                conversation.id,
                limit=10,
            )

            # PromptBuilder добавляет System Prompt + имя клиента + VIP статус
            ai_history = PromptBuilder.build_history(client, history_msgs)

            logger.info(
                "calling_gigachat",
                chat_id=chat_id,
                intent=intent,
                history_length=len(ai_history),
            )
            reply_text = await self.ai_client.generate_response(ai_history)

        # ========================================
        # ШАГ 4: Сохраняем outbound сообщение
        # ========================================
        await self.message_repo.create_message(
            conversation_id=conversation.id,
            direction="outbound",
            content=reply_text,
            is_escalation_trigger=should_escalate,
        )
        await self.db.commit()

        # ========================================
        # ШАГ 5: Отправка ответа в Telegram
        # ========================================
        try:
            await self.tg_client.send_message(chat_id, reply_text)
        except Exception as e:
            # Логируем, но не падаем — сообщение уже сохранено в БД
            # София увидит его в админ-панели и сможет ответить вручную
            logger.exception(
                "failed_to_send_reply",
                chat_id=chat_id,
                error=str(e),
            )
