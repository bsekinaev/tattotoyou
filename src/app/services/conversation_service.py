"""Platform-agnostic ядро обработки диалогов."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.platforms.base import PlatformAdapter, PlatformMessage
from app.domain.clients.models import Client
from app.domain.conversations.models import (
    CONVERSATION_ESCALATED,
    CONVERSATION_HUMAN_OWNED,
    Conversation,
)
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    MessageRepository,
    OutboundDeliveryRepository,
    PlatformRepository,
)
from app.services.ai.exceptions import GigaChatError
from app.services.ai.fallback_responder import FallbackResponder
from app.services.ai.gigachat_client import GigaChatClient
from app.services.ai.intent_classifier import IntentClassifier
from app.services.ai.knowledge_retriever import KnowledgeRetriever
from app.services.ai.prompt_builder import PromptBuilder
from app.services.escalation.engine import EscalationEngine
from app.services.notifications.admin_notifier import AdminNotifier
from app.workers.tasks.deliver_outbound_message import deliver_outbound_message_task

logger = get_logger(__name__)
settings = get_settings()


class ConversationService:
    """Обрабатывает входящие сообщения без привязки к конкретной платформе."""

    def __init__(
        self,
        db: AsyncSession,
        platform_repo: PlatformRepository,
        client_repo: ClientRepository,
        conversation_repo: ConversationRepository,
        message_repo: MessageRepository,
        platform_adapter: PlatformAdapter,
        ai_client: GigaChatClient,
        outbound_delivery_repo: OutboundDeliveryRepository | None = None,
        knowledge_retriever: KnowledgeRetriever | None = None,
    ):
        self.db = db
        self.platform_repo = platform_repo
        self.client_repo = client_repo
        self.conversation_repo = conversation_repo
        self.message_repo = message_repo
        self.platform_adapter = platform_adapter
        self.ai_client = ai_client
        self.outbound_delivery_repo = outbound_delivery_repo or OutboundDeliveryRepository(db)
        self.knowledge_retriever = knowledge_retriever or KnowledgeRetriever(db)

    async def process_message(
        self,
        message: PlatformMessage,
        *,
        causation_event_id: uuid.UUID | None = None,
    ) -> None:
        """Сохранить inbound и атомарно поставить ответ в Transactional Outbox."""
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
                delivery = await self.outbound_delivery_repo.get_by_message_id(existing_outbound.id)
                if delivery is None and existing_outbound.platform_message_id is None:
                    delivery, _created = await self.outbound_delivery_repo.create_for_message(
                        message_id=existing_outbound.id,
                        platform=message.platform,
                        destination_id=message.chat_id,
                    )
                await self.db.commit()
                if delivery is not None:
                    self._dispatch_delivery(delivery.id)
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

        if getattr(client, "is_banned", False):
            await self.conversation_repo.mark_spam(conversation)
            await self.db.commit()
            logger.warning(
                "banned_client_message_suppressed",
                client_id=client.id,
                conversation_id=str(conversation.id),
            )
            return

        if conversation.status in (CONVERSATION_ESCALATED, CONVERSATION_HUMAN_OWNED):
            logger.info(
                "ai_reply_suppressed_for_handoff",
                conversation_id=str(conversation.id),
                status=conversation.status,
            )
            return

        intent_result = IntentClassifier.classify_detailed(message.text)
        intent = intent_result.intent
        escalation = EscalationEngine.evaluate(
            intent_result,
            message.text,
            is_vip=bool(getattr(client, "is_vip", False)),
            low_confidence_threshold=settings.intent_low_confidence_threshold,
        )
        should_escalate = escalation.should_escalate
        reason = escalation.reason

        if should_escalate:
            logger.warning(
                "escalation_triggered",
                reason=reason,
                chat_id=message.chat_id,
                intent=intent,
                confidence=intent_result.confidence,
                matched_rules=intent_result.matched_rules,
            )
            await self.conversation_repo.escalate(conversation)
            reply_text = (
                "Отличный вопрос! Передам его Софии — она лично ответит в течение 15 минут 💛"
            )
        else:
            history_msgs = await self.message_repo.get_history(
                conversation.id,
                limit=settings.ai_history_max_messages,
            )
            knowledge_items = await self.knowledge_retriever.retrieve(message.text)

            if intent in settings.rag_required_intent_set and not knowledge_items:
                should_escalate = True
                reason = f"knowledge_missing:{intent}"
                await self.conversation_repo.escalate(conversation)
                reply_text = FallbackResponder.get_response(intent)
                logger.warning(
                    "required_knowledge_not_found",
                    intent=intent,
                    conversation_id=str(conversation.id),
                )
            else:
                ai_history = PromptBuilder.build_with_knowledge(
                    client,
                    history_msgs,
                    knowledge_items,
                    max_messages=settings.ai_history_max_messages,
                    max_chars=settings.ai_history_max_chars,
                )
                logger.info(
                    "calling_gigachat",
                    chat_id=message.chat_id,
                    intent=intent,
                    history_length=len(ai_history),
                    knowledge_items=len(knowledge_items),
                )
                try:
                    reply_text = await self.ai_client.generate_response(ai_history)
                except GigaChatError as exc:
                    should_escalate = True
                    reason = f"ai_unavailable:{exc.code}"
                    await self.conversation_repo.escalate(conversation)
                    reply_text = FallbackResponder.get_response(intent)
                    logger.error(
                        "gigachat_fallback_escalated",
                        error_code=exc.code,
                        retryable=exc.retryable,
                        conversation_id=str(conversation.id),
                    )

        reply_text = self._bounded_reply(reply_text)
        outbound_created = True
        if causation_event_id is None:
            outbound = await self.message_repo.create_message(
                conversation_id=conversation.id,
                direction="outbound",
                sender_type="bot",
                content=reply_text,
                is_escalation_trigger=should_escalate,
            )
        else:
            outbound, outbound_created = await self.message_repo.create_message_once(
                conversation_id=conversation.id,
                direction="outbound",
                sender_type="bot",
                content=reply_text,
                causation_event_id=causation_event_id,
                is_escalation_trigger=should_escalate,
            )

        delivery, delivery_created = await self.outbound_delivery_repo.create_for_message(
            message_id=outbound.id,
            platform=message.platform,
            destination_id=message.chat_id,
        )
        delivery_ids: list[uuid.UUID] = []
        if delivery_created or outbound_created:
            delivery_ids.append(delivery.id)

        if outbound_created:
            await self.conversation_repo.increment_ai_messages(conversation)

        if should_escalate and outbound_created:
            notification_text = AdminNotifier.build_outbox_message(
                client_name=client.display_name or "Гость",
                client_username=client.username,
                reason=reason,
                last_message=message.text or "",
                chat_id=message.chat_id,
            )
            deduplication_key = self._escalation_deduplication_key(
                conversation_id=conversation.id,
                outbound_message_id=outbound.id,
                causation_event_id=causation_event_id,
            )
            (
                notification,
                notification_created,
            ) = await self.outbound_delivery_repo.create_notification(
                platform="telegram",
                destination_id=str(settings.telegram_admin_chat_id),
                payload_text=notification_text,
                deduplication_key=deduplication_key,
            )
            if notification_created:
                delivery_ids.append(notification.id)

        await self.db.commit()

        if delivery_ids:
            for delivery_id in dict.fromkeys(delivery_ids):
                self._dispatch_delivery(delivery_id)
        else:
            logger.info(
                "duplicate_outbound_effect_suppressed",
                causation_event_id=str(causation_event_id),
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
            display_name=message.user.display_name,
            username=message.user.username,
        )
        conversation = await self.conversation_repo.get_or_create_active(client_id=client.id)
        await self.conversation_repo.update_activity(conversation)

        if causation_event_id is None:
            await self.message_repo.create_message(
                conversation_id=conversation.id,
                direction="inbound",
                sender_type="client",
                content=message.text or "",
                platform_message_id=message.message_id,
            )
        else:
            await self.message_repo.create_message_once(
                conversation_id=conversation.id,
                direction="inbound",
                sender_type="client",
                content=message.text or "",
                platform_message_id=message.message_id,
                causation_event_id=causation_event_id,
            )
        await self.db.commit()
        return client, conversation

    @staticmethod
    def _bounded_reply(reply_text: str) -> str:
        normalized = reply_text.strip()
        if len(normalized) <= settings.ai_response_max_chars:
            return normalized
        return normalized[: settings.ai_response_max_chars - 1].rstrip() + "…"

    @staticmethod
    def _dispatch_delivery(delivery_id: uuid.UUID) -> bool:
        try:
            deliver_outbound_message_task.delay(str(delivery_id))
            return True
        except Exception as exc:
            logger.exception(
                "outbound_delivery_dispatch_failed",
                delivery_id=str(delivery_id),
                error_type=type(exc).__name__,
            )
            return False

    @staticmethod
    def _escalation_deduplication_key(
        *,
        conversation_id: uuid.UUID,
        outbound_message_id: int,
        causation_event_id: uuid.UUID | None,
    ) -> str:
        if causation_event_id is not None:
            return f"escalation:event:{causation_event_id}"
        return f"escalation:conversation:{conversation_id}:message:{outbound_message_id}"
