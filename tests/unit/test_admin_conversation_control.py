"""Тесты административного управления handoff и Outbox."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.admin import conversations as admin_module
from app.domain.conversations.models import CONVERSATION_HUMAN_OWNED
from app.domain.outbound.models import OUTBOUND_DELIVERY_PENDING
from app.infrastructure.db.repositories.conversation_repository import (
    InvalidConversationTransitionError,
)


@pytest.mark.asyncio
async def test_takeover_transition_commits_state(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation = SimpleNamespace(
        id=uuid.uuid4(),
        client_id=1,
        status=CONVERSATION_HUMAN_OWNED,
        assigned_to_human=True,
        ai_messages_count=2,
        human_messages_count=0,
        last_activity_at=datetime.now(UTC),
        closed_at=None,
    )
    calls: list[str] = []
    db = SimpleNamespace(commit=AsyncMock(side_effect=lambda: calls.append("commit")))

    class Repository:
        def __init__(self, _db) -> None:
            pass

        async def take_over(self, _conversation_id):
            calls.append("transition")
            return conversation

    monkeypatch.setattr(admin_module, "ConversationRepository", Repository)

    result = await admin_module.take_over_conversation(conversation.id, db)

    assert result.status == CONVERSATION_HUMAN_OWNED
    assert calls == ["transition", "commit"]


@pytest.mark.asyncio
async def test_invalid_transition_returns_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    class Repository:
        def __init__(self, _db) -> None:
            pass

        async def return_to_ai(self, _conversation_id):
            raise InvalidConversationTransitionError("invalid state")

    monkeypatch.setattr(admin_module, "ConversationRepository", Repository)

    with pytest.raises(HTTPException) as exc_info:
        await admin_module.return_conversation_to_ai(
            uuid.uuid4(),
            SimpleNamespace(commit=AsyncMock()),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_manual_delivery_retry_commits_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = SimpleNamespace(
        id=uuid.uuid4(),
        message_id=10,
        platform="telegram",
        destination_id="123",
        status=OUTBOUND_DELIVERY_PENDING,
        attempts=0,
        next_attempt_at=None,
        last_error_code=None,
        platform_message_id=None,
        sent_at=None,
        failed_at=None,
    )
    calls: list[str] = []
    db = SimpleNamespace(commit=AsyncMock(side_effect=lambda: calls.append("commit")))

    class Repository:
        def __init__(self, _db) -> None:
            pass

        async def retry_failed(self, _delivery_id):
            calls.append("retry")
            return delivery

    monkeypatch.setattr(admin_module, "OutboundDeliveryRepository", Repository)
    monkeypatch.setattr(
        admin_module.deliver_outbound_message_task,
        "delay",
        MagicMock(side_effect=lambda _delivery_id: calls.append("dispatch")),
    )

    result = await admin_module.retry_failed_delivery(delivery.id, db)

    assert result.status == OUTBOUND_DELIVERY_PENDING
    assert calls == ["retry", "commit", "dispatch"]
