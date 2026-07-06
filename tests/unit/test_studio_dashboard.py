"""Тесты продуктовых операций панели Сони."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import studio_dashboard as dashboard_module
from app.services.studio_dashboard import StudioDashboardService


class Result:
    def __init__(self, *, row=None, scalar=None):
        self.row = row
        self.scalar = scalar

    def one_or_none(self):
        return self.row

    def scalar_one_or_none(self):
        return self.scalar


@pytest.mark.asyncio
async def test_human_reply_commits_outbox_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation_id = uuid.uuid4()
    delivery_id = uuid.uuid4()
    conversation = SimpleNamespace(
        id=conversation_id,
        client_id=12,
        status="human_owned",
        last_read_at=None,
    )
    client = SimpleNamespace(id=12, external_id="telegram-chat")
    platform = SimpleNamespace(name="telegram")
    message = SimpleNamespace(id=55)
    delivery = SimpleNamespace(id=delivery_id)
    calls: list[str] = []

    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                Result(row=(client, platform)),
                Result(scalar="known-chat-id"),
            ]
        ),
        commit=AsyncMock(side_effect=lambda: calls.append("commit")),
    )

    class ConversationRepository:
        def __init__(self, _db):
            pass

        async def take_over(self, supplied_id):
            assert supplied_id == conversation_id
            calls.append("takeover")
            return conversation

        async def increment_human_messages(self, supplied_conversation):
            assert supplied_conversation is conversation
            calls.append("increment")

        async def update_activity(self, supplied_conversation):
            assert supplied_conversation is conversation
            calls.append("activity")

    class MessageRepository:
        def __init__(self, _db):
            pass

        async def create_message(self, **kwargs):
            assert kwargs["sender_type"] == "human"
            assert kwargs["content"] == "Здравствуйте!"
            calls.append("message")
            return message

    class OutboundRepository:
        def __init__(self, _db):
            pass

        async def create_for_message(self, **kwargs):
            assert kwargs == {
                "message_id": 55,
                "platform": "telegram",
                "destination_id": "known-chat-id",
            }
            calls.append("outbox")
            return delivery, True

    monkeypatch.setattr(dashboard_module, "ConversationRepository", ConversationRepository)
    monkeypatch.setattr(dashboard_module, "MessageRepository", MessageRepository)
    monkeypatch.setattr(dashboard_module, "OutboundDeliveryRepository", OutboundRepository)
    monkeypatch.setattr(
        dashboard_module.deliver_outbound_message_task,
        "delay",
        MagicMock(side_effect=lambda _delivery_id: calls.append("dispatch")),
    )

    result = await StudioDashboardService(db).send_human_reply(
        conversation_id,
        "  Здравствуйте!  ",
    )

    assert result is delivery
    assert calls == [
        "takeover",
        "message",
        "outbox",
        "increment",
        "activity",
        "commit",
        "dispatch",
    ]


@pytest.mark.asyncio
async def test_client_profile_is_normalized_and_committed() -> None:
    client = SimpleNamespace(
        lead_status="new",
        internal_notes=None,
        tattoo_idea=None,
        placement=None,
        size_details=None,
        style_preferences=None,
        budget_details=None,
        desired_date=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=Result(scalar=client)),
        commit=AsyncMock(),
    )

    result = await StudioDashboardService(db).update_client_profile(
        uuid.uuid4(),
        lead_status="consultation",
        internal_notes="  Любит минимализм  ",
        tattoo_idea="  ветка сакуры ",
        placement=" предплечье ",
        size_details=" 10×15 см ",
        style_preferences=" графика ",
        budget_details=" до 15000 ₽ ",
        desired_date=date(2026, 8, 10),
    )

    assert result.lead_status == "consultation"
    assert result.internal_notes == "Любит минимализм"
    assert result.tattoo_idea == "ветка сакуры"
    assert result.placement == "предплечье"
    assert result.desired_date == date(2026, 8, 10)
    statement = db.execute.await_args.args[0]
    assert "conversations.id" in str(statement)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_profile_rejects_unknown_lead_status() -> None:
    service = StudioDashboardService(SimpleNamespace())

    with pytest.raises(ValueError, match="Unknown lead status"):
        await service.update_client_profile(
            uuid.uuid4(),
            lead_status="mystery",
            internal_notes=None,
            tattoo_idea=None,
            placement=None,
            size_details=None,
            style_preferences=None,
            budget_details=None,
            desired_date=None,
        )


@pytest.mark.asyncio
async def test_list_query_contains_unread_and_search_predicates() -> None:
    class RowsResult:
        def all(self):
            return []

    db = SimpleNamespace(execute=AsyncMock(return_value=RowsResult()))

    items = await StudioDashboardService(db).list_conversations(
        status_filter="escalated",
        search="сакура",
    )

    assert items == []
    statement = db.execute.await_args.args[0]
    compiled = str(statement)
    assert "messages.direction" in compiled
    assert "conversations.last_read_at" in compiled
    assert "clients.tattoo_idea" in compiled
    assert "conversations.status" in compiled
