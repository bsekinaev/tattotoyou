"""Регрессии AI fallback, banned-профилей и лимитов контекста."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.clients.models import Client
from app.domain.conversations.models import CONVERSATION_ACTIVE, Conversation, Message
from app.services import conversation_service as service_module
from app.services.ai.exceptions import GigaChatServerError
from app.services.ai.intent_classifier import IntentResult
from app.services.ai.prompt_builder import PromptBuilder
from app.services.conversation_service import ConversationService
from app.services.escalation.engine import EscalationDecision


def _conversation() -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        client_id=1,
        status=CONVERSATION_ACTIVE,
        assigned_to_human=False,
        ai_messages_count=0,
        human_messages_count=0,
        last_activity_at=datetime.now(UTC),
    )


def _message(text: str = "Привет") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        platform="telegram",
        chat_id="123",
        message_id="55",
        user=SimpleNamespace(external_id="7", display_name="Анна", username="anna"),
    )


@pytest.mark.asyncio
async def test_banned_client_is_marked_spam_without_ai_or_outbox() -> None:
    conversation = _conversation()
    client = SimpleNamespace(id=1, is_banned=True)
    conversation_repo = SimpleNamespace(mark_spam=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    ai_client = SimpleNamespace(generate_response=AsyncMock())
    outbox = SimpleNamespace(create_for_message=AsyncMock())
    service = ConversationService(
        db=db,
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=conversation_repo,
        message_repo=SimpleNamespace(get_by_causation=AsyncMock(return_value=None)),
        platform_adapter=SimpleNamespace(),
        ai_client=ai_client,
        outbound_delivery_repo=outbox,
    )
    service._resolve_conversation = AsyncMock(return_value=(client, conversation))

    await service.process_message(_message(), causation_event_id=uuid.uuid4())

    conversation_repo.mark_spam.assert_awaited_once_with(conversation)
    ai_client.generate_response.assert_not_awaited()
    outbox.create_for_message.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_final_ai_failure_creates_fallback_outbox_and_notifies_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = uuid.uuid4()
    conversation = _conversation()
    client = SimpleNamespace(
        id=1,
        display_name="Анна",
        username="anna",
        is_vip=False,
        is_banned=False,
    )
    outbound = SimpleNamespace(id=10)
    delivery = SimpleNamespace(id=uuid.uuid4())
    notification = SimpleNamespace(id=uuid.uuid4())
    db = SimpleNamespace(commit=AsyncMock())
    conversation_repo = SimpleNamespace(
        escalate=AsyncMock(),
        increment_ai_messages=AsyncMock(),
    )
    message_repo = SimpleNamespace(
        get_by_causation=AsyncMock(return_value=None),
        get_history=AsyncMock(return_value=[]),
        create_message_once=AsyncMock(return_value=(outbound, True)),
    )
    outbox_repo = SimpleNamespace(
        create_for_message=AsyncMock(return_value=(delivery, True)),
        create_notification=AsyncMock(return_value=(notification, True)),
    )
    ai_client = SimpleNamespace(
        generate_response=AsyncMock(
            side_effect=GigaChatServerError(
                "unavailable",
                code="completion_server_error",
                retryable=True,
                status_code=503,
            )
        )
    )
    service = ConversationService(
        db=db,
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=conversation_repo,
        message_repo=message_repo,
        platform_adapter=SimpleNamespace(),
        ai_client=ai_client,
        outbound_delivery_repo=outbox_repo,
        knowledge_retriever=SimpleNamespace(
            retrieve=AsyncMock(
                return_value=[
                    {
                        "question": "Сколько стоит тату?",
                        "answer": "Стоимость уточняется после обсуждения эскиза.",
                    }
                ]
            )
        ),
    )
    service._resolve_conversation = AsyncMock(return_value=(client, conversation))
    monkeypatch.setattr(
        service_module.IntentClassifier,
        "classify_detailed",
        lambda _text: IntentResult("pricing", 0.95, ("сколько стоит",), {"pricing": 5}),
    )
    monkeypatch.setattr(
        service_module.EscalationEngine,
        "evaluate",
        lambda *_args, **_kwargs: EscalationDecision(False),
    )
    dispatch = MagicMock()
    monkeypatch.setattr(service_module.deliver_outbound_message_task, "delay", dispatch)

    await service.process_message(_message("Сколько стоит?"), causation_event_id=event_id)

    conversation_repo.escalate.assert_awaited_once_with(conversation)
    kwargs = message_repo.create_message_once.await_args.kwargs
    assert "не могу надёжно подтвердить стоимость" in kwargs["content"]
    assert kwargs["is_escalation_trigger"] is True
    notification_kwargs = outbox_repo.create_notification.await_args.kwargs
    assert notification_kwargs["destination_id"] == str(
        service_module.settings.telegram_admin_chat_id
    )
    assert "ai_unavailable:completion_server_error" in notification_kwargs["payload_text"]
    assert notification_kwargs["deduplication_key"] == f"escalation:event:{event_id}"
    assert [call.args[0] for call in dispatch.call_args_list] == [
        str(delivery.id),
        str(notification.id),
    ]


def test_prompt_history_keeps_latest_messages_within_character_budget() -> None:
    client = Client(id=1, platform_id=1, external_id="7", display_name=None, is_vip=False)
    messages = [
        Message(direction="inbound", content="a" * 10),
        Message(direction="outbound", content="b" * 10),
        Message(direction="inbound", content="c" * 10),
    ]

    history = PromptBuilder.build_history(client, messages, max_messages=3, max_chars=15)

    dialog = history[1:]
    assert [item["role"] for item in dialog] == ["assistant", "user"]
    assert dialog[0]["content"] == "b" * 5
    assert dialog[1]["content"] == "c" * 10


def test_ai_reply_is_bounded_for_single_telegram_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.settings, "ai_response_max_chars", 20)

    result = ConversationService._bounded_reply("x" * 100)

    assert len(result) == 20
    assert result.endswith("…")


@pytest.mark.asyncio
async def test_escalation_persists_client_reply_and_admin_notification_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    event_id = uuid.uuid4()
    conversation = _conversation()
    client = SimpleNamespace(
        id=1,
        display_name="Анна",
        username="anna",
        is_vip=False,
        is_banned=False,
    )
    outbound = SimpleNamespace(id=10)
    client_delivery = SimpleNamespace(id=uuid.uuid4())
    admin_delivery = SimpleNamespace(id=uuid.uuid4())
    db = SimpleNamespace(commit=AsyncMock(side_effect=lambda: calls.append("commit")))
    conversation_repo = SimpleNamespace(
        escalate=AsyncMock(),
        increment_ai_messages=AsyncMock(),
    )
    message_repo = SimpleNamespace(
        get_by_causation=AsyncMock(return_value=None),
        create_message_once=AsyncMock(return_value=(outbound, True)),
    )

    async def create_client_delivery(**_kwargs: object) -> tuple[SimpleNamespace, bool]:
        calls.append("create_client_delivery")
        return client_delivery, True

    async def create_admin_notification(**kwargs: object) -> tuple[SimpleNamespace, bool]:
        assert "auto_escalation_health" in str(kwargs["payload_text"])
        calls.append("create_admin_notification")
        return admin_delivery, True

    outbox_repo = SimpleNamespace(
        create_for_message=AsyncMock(side_effect=create_client_delivery),
        create_notification=AsyncMock(side_effect=create_admin_notification),
    )
    service = ConversationService(
        db=db,
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=conversation_repo,
        message_repo=message_repo,
        platform_adapter=SimpleNamespace(),
        ai_client=SimpleNamespace(generate_response=AsyncMock()),
        outbound_delivery_repo=outbox_repo,
        knowledge_retriever=SimpleNamespace(retrieve=AsyncMock()),
    )
    service._resolve_conversation = AsyncMock(return_value=(client, conversation))
    monkeypatch.setattr(
        service_module.deliver_outbound_message_task,
        "delay",
        MagicMock(side_effect=lambda delivery_id: calls.append(f"dispatch:{delivery_id}")),
    )

    await service.process_message(
        _message("У меня диабет, можно тату?"),
        causation_event_id=event_id,
    )

    assert calls == [
        "create_client_delivery",
        "create_admin_notification",
        "commit",
        f"dispatch:{client_delivery.id}",
        f"dispatch:{admin_delivery.id}",
    ]
    conversation_repo.escalate.assert_awaited_once_with(conversation)
    service.ai_client.generate_response.assert_not_awaited()
