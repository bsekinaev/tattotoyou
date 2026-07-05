"""Admin API управления handoff-состояниями и исходящими доставками."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin.auth import require_admin
from app.api.admin.schemas import ConversationStateResponse, OutboundDeliveryResponse
from app.core.logging import get_logger
from app.domain.conversations.models import OPEN_CONVERSATION_STATUSES, Conversation
from app.domain.outbound.models import OutboundDelivery
from app.infrastructure.db.repositories import (
    ConversationRepository,
    OutboundDeliveryRepository,
)
from app.infrastructure.db.repositories.conversation_repository import (
    InvalidConversationTransitionError,
)
from app.infrastructure.db.session import get_db_session
from app.workers.tasks.deliver_outbound_message import deliver_outbound_message_task

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/conversations", response_model=list[ConversationStateResponse])
async def list_conversations(
    conversation_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> list[ConversationStateResponse]:
    query = select(Conversation).order_by(Conversation.last_activity_at.desc()).limit(limit)
    if conversation_status is not None:
        query = query.where(Conversation.status == conversation_status)
    else:
        query = query.where(Conversation.status.in_(OPEN_CONVERSATION_STATUSES))
    conversations = list((await db.execute(query)).scalars().all())
    return [_conversation_response(item) for item in conversations]


@router.post(
    "/conversations/{conversation_id}/takeover",
    response_model=ConversationStateResponse,
)
async def take_over_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> ConversationStateResponse:
    conversation = await _transition(
        db,
        conversation_id,
        action="takeover",
    )
    return _conversation_response(conversation)


@router.post(
    "/conversations/{conversation_id}/return-to-ai",
    response_model=ConversationStateResponse,
)
async def return_conversation_to_ai(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> ConversationStateResponse:
    conversation = await _transition(
        db,
        conversation_id,
        action="return_to_ai",
    )
    return _conversation_response(conversation)


@router.post(
    "/conversations/{conversation_id}/close",
    response_model=ConversationStateResponse,
)
async def close_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> ConversationStateResponse:
    conversation = await _transition(
        db,
        conversation_id,
        action="close",
    )
    return _conversation_response(conversation)


@router.get(
    "/deliveries",
    response_model=list[OutboundDeliveryResponse],
)
async def list_deliveries(
    delivery_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> list[OutboundDeliveryResponse]:
    query = select(OutboundDelivery).order_by(OutboundDelivery.created_at.desc()).limit(limit)
    if delivery_status is not None:
        query = query.where(OutboundDelivery.status == delivery_status)
    deliveries = list((await db.execute(query)).scalars().all())
    return [_delivery_response(item) for item in deliveries]


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=OutboundDeliveryResponse,
)
async def retry_failed_delivery(
    delivery_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> OutboundDeliveryResponse:
    repo = OutboundDeliveryRepository(db)
    try:
        delivery = await repo.retry_failed(delivery_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Outbound delivery not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await db.commit()
    try:
        deliver_outbound_message_task.delay(str(delivery.id))
    except Exception as exc:
        logger.exception(
            "manual_delivery_dispatch_failed",
            delivery_id=str(delivery.id),
            error_type=type(exc).__name__,
        )
    return _delivery_response(delivery)


async def _transition(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    action: str,
) -> Conversation:
    repo = ConversationRepository(db)
    try:
        if action == "takeover":
            conversation = await repo.take_over(conversation_id)
        elif action == "return_to_ai":
            conversation = await repo.return_to_ai(conversation_id)
        elif action == "close":
            conversation = await repo.close_conversation(conversation_id)
        else:
            raise RuntimeError("Unsupported conversation transition")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except InvalidConversationTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await db.commit()
    logger.info(
        "conversation_state_changed",
        conversation_id=str(conversation.id),
        status=conversation.status,
        action=action,
    )
    return conversation


def _conversation_response(conversation: Conversation) -> ConversationStateResponse:
    return ConversationStateResponse(
        id=str(conversation.id),
        client_id=conversation.client_id,
        status=conversation.status,
        assigned_to_human=conversation.assigned_to_human,
        ai_messages_count=conversation.ai_messages_count,
        human_messages_count=conversation.human_messages_count,
        last_activity_at=conversation.last_activity_at,
        closed_at=conversation.closed_at,
    )


def _delivery_response(delivery: OutboundDelivery) -> OutboundDeliveryResponse:
    return OutboundDeliveryResponse(
        id=str(delivery.id),
        message_id=delivery.message_id,
        platform=delivery.platform,
        destination_id=delivery.destination_id,
        status=delivery.status,
        attempts=delivery.attempts,
        next_attempt_at=delivery.next_attempt_at,
        last_error_code=delivery.last_error_code,
        platform_message_id=delivery.platform_message_id,
        sent_at=delivery.sent_at,
        failed_at=delivery.failed_at,
    )
