"""
Conversation Service — ядро бизнес-логики.

Работает с PlatformMessage и PlatformAdapter, не зная деталей конкретной платформы.
Это позволяет использовать одну и ту же логику для Telegram, VK, Instagram.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.platforms.base import PlatformAdapter, PlatformMessage
from app.domain.clients.models import Client
from app.domain.conversations.models import Conversation
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

    async def process_message(
        self,
        message: PlatformMessage,
        *,
        causation_event_id: uuid.UUID | None = None,
    ) -> None:
        """Обработать входящее сообщение от любой платформы.

        ``causation_event_id`` связывает входящее событие с единственным
        inbound и outbound сообщением. Повторный запуск Celery-задачи не
        создаёт второй бизнес-ответ в PostgreSQL.
        """
        if not message.text:
            return

        logger.info(
            "processing_message",
            platform=message.platform,
            chat_id=message.chat_id,
            user_id=message.user.external_id,
            causation_event_id=str(causation_event_id) if causation_event_id else None,
        )

        if causation_event_id is not None:
            existing_outbound = await self.message_repo.get_by_causation(
                causation_event_id,
                "outbound",
            )
            if existing_outbound is not None:
                logger.info(
                    "incoming_event_business_effect_already_exists",
                    causation_event_id=str(causation_event_id),
                    outbound_message_id=existing_outbound.id,
                )
                return

        client, conversation = await self._resolve_conversation(
            message,
            causation_event_id=causation_event_id,
        )

        # Intent Classification
        intent = IntentClassifier.classify(message.text)
        should_escalate, reason = EscalationEngine.should_escalate(intent, message.text)

        # Routing: Escalation vs AI
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
        else:
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

        outbound_created = True
        if causation_event_id is None:
            await self.message_repo.create_message(
                conversation_id=conversation.id,
                direction="outbound",
                content=reply_text,
                is_escalation_trigger=should_escalate,
            )
        else:
            _, outbound_created = await self.message_repo.create_message_once(
                conversation_id=conversation.id,
                direction="outbound",
                content=reply_text,
                causation_event_id=causation_event_id,
                is_escalation_trigger=should_escalate,
            )
        await self.db.commit()

        if not outbound_created:
            logger.info(
                "duplicate_outbound_effect_suppressed",
                causation_event_id=str(causation_event_id),
            )
            return

        if should_escalate:
            try:
                send_admin_notification_task.delay(
                    client_name=client.display_name or "Гость",
                    client_username=client.username,
                    reason=reason,
                    last_message=message.text,
                    chat_id=int(message.chat_id),
                )
            except Exception as exc:
                logger.exception(
                    "admin_notification_dispatch_failed",
                    chat_id=message.chat_id,
                    error_type=type(exc).__name__,
                )

        try:
            await self.platform_adapter.send_message(message.chat_id, reply_text)
            logger.info("reply_sent", chat_id=message.chat_id, platform=message.platform)
        except Exception as exc:
            # Надёжная доставка будет вынесена в Transactional Outbox.
            logger.exception(
                "failed_to_send_reply",
                chat_id=message.chat_id,
                platform=message.platform,
                error_type=type(exc).__name__,
            )

    async def _resolve_conversation(
        self,
        message: PlatformMessage,
        *,
        causation_event_id: uuid.UUID | None,
    ) -> tuple[Client, Conversation]:
        """Найти контекст повтора или создать новый inbound-эффект."""
        if causation_event_id is not None:
            existing_inbound = await self.message_repo.get_by_causation(
                causation_event_id,
                "inbound",
            )
            if existing_inbound is not None:
                conversation = await self.conversation_repo.get_by_id(
                    existing_inbound.conversation_id
                )
                if conversation is None:
                    raise RuntimeError("Causation conversation does not exist")
                client = await self.client_repo.get_by_id(conversation.client_id)
                if client is None:
                    raise RuntimeError("Causation client does not exist")
                return client, conversation

        platform = await self.platform_repo.get_or_create(
            name=message.platform,
            webhook_secret="",
        )
        client = await self.client_repo.get_or_create(
            platform_id=platform.id,
            external_id=message.user.external_id,
            display_name=message.user.display_name or "Гость",
            username=message.user.username,
        )
        conversation = await self.conversation_repo.get_or_create_active(client_id=client.id)
        await self.conversation_repo.update_activity(conversation)

        if causation_event_id is None:
            await self.message_repo.create_message(
                conversation_id=conversation.id,
                direction="inbound",
                content=message.text or "",
                platform_message_id=message.message_id,
            )
        else:
            await self.message_repo.create_message_once(
                conversation_id=conversation.id,
                direction="inbound",
                content=message.text or "",
                platform_message_id=message.message_id,
                causation_event_id=causation_event_id,
            )
        await self.db.commit()
        return client, conversation
