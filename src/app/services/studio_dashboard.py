"""Продуктовый слой web-панели Сони."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.logging import get_logger
from app.domain.bookings.models import TattooApplication
from app.domain.clients.models import Client, Platform
from app.domain.conversations.models import (
    CONVERSATION_ACTIVE,
    CONVERSATION_ESCALATED,
    CONVERSATION_HUMAN_OWNED,
    OPEN_CONVERSATION_STATUSES,
    Conversation,
    Message,
)
from app.domain.outbound.models import OutboundDelivery
from app.infrastructure.db.repositories import (
    ConversationRepository,
    MessageRepository,
    OutboundDeliveryRepository,
)
from app.infrastructure.db.repositories.conversation_repository import (
    InvalidConversationTransitionError,
)
from app.workers.tasks.deliver_outbound_message import deliver_outbound_message_task

logger = get_logger(__name__)

LEAD_STATUSES = (
    "new",
    "qualification",
    "consultation",
    "waiting_payment",
    "booked",
    "completed",
    "lost",
)

CONVERSATION_FILTERS = (
    "open",
    CONVERSATION_ACTIVE,
    CONVERSATION_ESCALATED,
    CONVERSATION_HUMAN_OWNED,
    "closed",
    "spam",
    "all",
)


@dataclass(slots=True)
class ConversationListItem:
    id: uuid.UUID
    client_id: int
    display_name: str
    username: str | None
    external_id: str
    platform: str
    status: str
    lead_status: str
    assigned_to_human: bool
    last_activity_at: datetime
    latest_message: str | None
    latest_sender_type: str | None
    unread_count: int


@dataclass(slots=True)
class ConversationDetail:
    conversation: Conversation
    client: Client
    platform: Platform
    messages: list[Message]
    deliveries: dict[int, OutboundDelivery]
    applications: list[TattooApplication]


class StudioDashboardService:
    """Чтение рабочего стола и изменяющие операции мастера."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_conversations(
        self,
        *,
        status_filter: str = "open",
        search: str | None = None,
        limit: int = 100,
    ) -> list[ConversationListItem]:
        if status_filter not in CONVERSATION_FILTERS:
            status_filter = "open"

        latest_content = (
            select(Message.content)
            .where(Message.conversation_id == Conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
            .correlate(Conversation)
            .scalar_subquery()
        )
        latest_sender = (
            select(Message.sender_type)
            .where(Message.conversation_id == Conversation.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
            .correlate(Conversation)
            .scalar_subquery()
        )
        unread_count = (
            select(func.count(Message.id))
            .where(
                Message.conversation_id == Conversation.id,
                Message.direction == "inbound",
                or_(
                    Conversation.last_read_at.is_(None),
                    Message.created_at > Conversation.last_read_at,
                ),
            )
            .correlate(Conversation)
            .scalar_subquery()
        )

        query = (
            select(
                Conversation,
                Client,
                Platform,
                latest_content.label("latest_content"),
                latest_sender.label("latest_sender"),
                unread_count.label("unread_count"),
            )
            .join(Client, Client.id == Conversation.client_id)
            .join(Platform, Platform.id == Client.platform_id)
            .order_by(
                case(
                    (Conversation.status == CONVERSATION_ESCALATED, 0),
                    (Conversation.status == CONVERSATION_HUMAN_OWNED, 1),
                    (Conversation.status == CONVERSATION_ACTIVE, 2),
                    else_=3,
                ),
                Conversation.last_activity_at.desc(),
            )
            .limit(limit)
        )

        if status_filter == "open":
            query = query.where(Conversation.status.in_(OPEN_CONVERSATION_STATUSES))
        elif status_filter != "all":
            query = query.where(Conversation.status == status_filter)

        normalized_search = (search or "").strip()
        if normalized_search:
            pattern = f"%{normalized_search}%"
            message_match = exists(
                select(Message.id).where(
                    Message.conversation_id == Conversation.id,
                    Message.content.ilike(pattern),
                )
            )
            query = query.where(
                or_(
                    Client.display_name.ilike(pattern),
                    Client.username.ilike(pattern),
                    Client.external_id.ilike(pattern),
                    Client.tattoo_idea.ilike(pattern),
                    message_match,
                )
            )

        rows = (await self.db.execute(query)).all()
        return [
            ConversationListItem(
                id=conversation.id,
                client_id=client.id,
                display_name=client.display_name or "Гость",
                username=client.username,
                external_id=client.external_id,
                platform=platform.name,
                status=conversation.status,
                lead_status=client.lead_status,
                assigned_to_human=conversation.assigned_to_human,
                last_activity_at=conversation.last_activity_at,
                latest_message=latest_message,
                latest_sender_type=latest_sender_type,
                unread_count=int(unread or 0),
            )
            for conversation, client, platform, latest_message, latest_sender_type, unread in rows
        ]

    async def get_conversation_detail(
        self,
        conversation_id: uuid.UUID,
        *,
        mark_read: bool = True,
    ) -> ConversationDetail:
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(
                joinedload(Conversation.client).joinedload(Client.platform),
                selectinload(Conversation.messages),
                selectinload(Conversation.tattoo_applications),
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise LookupError("Conversation does not exist")

        messages = list(conversation.messages)
        deliveries: dict[int, OutboundDelivery] = {}
        message_ids = [message.id for message in messages if message.direction == "outbound"]
        if message_ids:
            delivery_rows = await self.db.execute(
                select(OutboundDelivery).where(OutboundDelivery.message_id.in_(message_ids))
            )
            deliveries = {
                delivery.message_id: delivery for delivery in delivery_rows.scalars().all()
            }

        if mark_read:
            conversation.last_read_at = datetime.now(UTC)
            await self.db.flush()

        return ConversationDetail(
            conversation=conversation,
            client=conversation.client,
            platform=conversation.client.platform,
            messages=messages,
            deliveries=deliveries,
            applications=list(conversation.tattoo_applications),
        )

    async def send_human_reply(
        self,
        conversation_id: uuid.UUID,
        content: str,
    ) -> OutboundDelivery:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Reply cannot be empty")
        if len(normalized_content) > 4000:
            raise ValueError("Reply is too long")

        conversation_repo = ConversationRepository(self.db)
        conversation = await conversation_repo.take_over(conversation_id)

        client_result = await self.db.execute(
            select(Client, Platform)
            .join(Platform, Platform.id == Client.platform_id)
            .where(Client.id == conversation.client_id)
        )
        client_row = client_result.one_or_none()
        if client_row is None:
            raise LookupError("Conversation client does not exist")
        client, platform = client_row

        destination_id = await self._resolve_destination_id(conversation.id, client.external_id)
        message = await MessageRepository(self.db).create_message(
            conversation_id=conversation.id,
            direction="outbound",
            sender_type="human",
            content=normalized_content,
        )
        delivery, _created = await OutboundDeliveryRepository(self.db).create_for_message(
            message_id=message.id,
            platform=platform.name,
            destination_id=destination_id,
        )
        await conversation_repo.increment_human_messages(conversation)
        await conversation_repo.update_activity(conversation)
        conversation.last_read_at = datetime.now(UTC)
        await self.db.commit()

        try:
            deliver_outbound_message_task.delay(str(delivery.id))
        except Exception as exc:
            logger.exception(
                "studio_reply_dispatch_failed",
                conversation_id=str(conversation.id),
                delivery_id=str(delivery.id),
                error_type=type(exc).__name__,
            )

        logger.info(
            "studio_human_reply_created",
            conversation_id=str(conversation.id),
            delivery_id=str(delivery.id),
        )
        return delivery

    async def update_client_profile(
        self,
        conversation_id: uuid.UUID,
        *,
        lead_status: str,
        internal_notes: str | None,
        tattoo_idea: str | None,
        placement: str | None,
        size_details: str | None,
        style_preferences: str | None,
        budget_details: str | None,
        desired_date: date | None,
    ) -> Client:
        if lead_status not in LEAD_STATUSES:
            raise ValueError("Unknown lead status")

        result = await self.db.execute(
            select(Client)
            .join(Conversation, Conversation.client_id == Client.id)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        )
        client = result.scalar_one_or_none()
        if client is None:
            raise LookupError("Client does not exist")

        client.lead_status = lead_status
        client.internal_notes = _optional_text(internal_notes, 5000)
        client.tattoo_idea = _optional_text(tattoo_idea, 5000)
        client.placement = _optional_text(placement, 120)
        client.size_details = _optional_text(size_details, 120)
        client.style_preferences = _optional_text(style_preferences, 200)
        client.budget_details = _optional_text(budget_details, 120)
        client.desired_date = desired_date
        await self.db.commit()
        return client

    async def transition(self, conversation_id: uuid.UUID, action: str) -> Conversation:
        repo = ConversationRepository(self.db)
        if action == "takeover":
            conversation = await repo.take_over(conversation_id)
        elif action == "return_to_ai":
            conversation = await repo.return_to_ai(conversation_id)
        elif action == "close":
            conversation = await repo.close_conversation(conversation_id)
        else:
            raise InvalidConversationTransitionError("Unsupported studio action")
        await self.db.commit()
        return conversation

    async def _resolve_destination_id(
        self,
        conversation_id: uuid.UUID,
        fallback_external_id: str,
    ) -> str:
        result = await self.db.execute(
            select(OutboundDelivery.destination_id)
            .join(Message, Message.id == OutboundDelivery.message_id)
            .where(Message.conversation_id == conversation_id)
            .order_by(OutboundDelivery.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none() or fallback_external_id


def _optional_text(value: str | None, max_length: int) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise ValueError(f"Value exceeds {max_length} characters")
    return normalized
