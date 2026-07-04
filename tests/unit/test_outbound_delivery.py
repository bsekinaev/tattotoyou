"""Регрессионные тесты Transactional Outbox исходящих сообщений."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.core.platforms.exceptions import PlatformHTTPError, PlatformTransportError
from app.domain.conversations.models import (
    CONVERSATION_ACTIVE,
    CONVERSATION_ESCALATED,
    CONVERSATION_HUMAN_OWNED,
    Conversation,
)
from app.domain.outbound.models import (
    OUTBOUND_DELIVERY_FAILED,
    OUTBOUND_DELIVERY_PENDING,
    OUTBOUND_DELIVERY_RETRYING,
    OUTBOUND_DELIVERY_SENDING,
    OutboundDelivery,
)
from app.infrastructure.db.repositories.conversation_repository import (
    ConversationRepository,
    InvalidConversationTransitionError,
)
from app.infrastructure.db.repositories.outbound_delivery_repository import (
    OutboundDeliveryRepository,
)
from app.services import conversation_service as conversation_service_module
from app.services.conversation_service import ConversationService
from app.workers.tasks import deliver_outbound_message as delivery_task_module


class ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class FakeSession:
    def __init__(self, results: list[Any]) -> None:
        self.execute = AsyncMock(side_effect=results)
        self.flush = AsyncMock()
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


def _delivery(**overrides: Any) -> OutboundDelivery:
    values = {
        "id": uuid.uuid4(),
        "message_id": 10,
        "platform": "telegram",
        "destination_id": "123",
        "status": OUTBOUND_DELIVERY_PENDING,
        "attempts": 0,
    }
    values.update(overrides)
    return OutboundDelivery(**values)


def _conversation(**overrides: Any) -> Conversation:
    values = {
        "id": uuid.uuid4(),
        "client_id": 1,
        "status": CONVERSATION_ACTIVE,
        "assigned_to_human": False,
        "ai_messages_count": 0,
        "human_messages_count": 0,
        "last_activity_at": datetime.now(UTC),
    }
    values.update(overrides)
    return Conversation(**values)


@pytest.mark.asyncio
async def test_delivery_create_uses_message_upsert() -> None:
    existing = _delivery()
    session = FakeSession([ScalarResult(None), ScalarResult(existing)])

    delivery, created = await OutboundDeliveryRepository(session).create_for_message(
        message_id=10,
        platform="telegram",
        destination_id="123",
    )

    assert delivery is existing
    assert created is False
    statement = session.execute.await_args_list[0].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "ON CONFLICT (message_id) DO NOTHING" in str(compiled)
    assert "RETURNING outbound_deliveries.id" in str(compiled)


@pytest.mark.asyncio
async def test_delivery_claim_uses_skip_locked_and_marks_sending() -> None:
    delivery = _delivery()
    session = FakeSession([ScalarResult(delivery)])
    now = datetime.now(UTC)

    claimed = await OutboundDeliveryRepository(session).claim(
        delivery.id,
        processing_timeout=timedelta(minutes=5),
        now=now,
    )

    assert claimed is delivery
    assert delivery.status == OUTBOUND_DELIVERY_SENDING
    assert delivery.attempts == 1
    assert delivery.processing_started_at == now
    statement = session.execute.await_args_list[0].args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_failure_distinguishes_retry_and_permanent_failure() -> None:
    now = datetime.now(UTC)
    retrying = _delivery(status=OUTBOUND_DELIVERY_SENDING, attempts=2)
    retry_session = FakeSession([ScalarResult(retrying)])

    result = await OutboundDeliveryRepository(retry_session).mark_failure(
        retrying.id,
        error_code="PlatformHTTP500",
        max_attempts=5,
        retry_delay=timedelta(seconds=30),
        permanent=False,
        now=now,
    )
    assert result.status == OUTBOUND_DELIVERY_RETRYING
    assert result.next_attempt_at == now + timedelta(seconds=30)

    failed = _delivery(status=OUTBOUND_DELIVERY_SENDING, attempts=1)
    failed_session = FakeSession([ScalarResult(failed)])
    result = await OutboundDeliveryRepository(failed_session).mark_failure(
        failed.id,
        error_code="PlatformHTTP400",
        max_attempts=5,
        retry_delay=timedelta(seconds=30),
        permanent=True,
        now=now,
    )
    assert result.status == OUTBOUND_DELIVERY_FAILED
    assert result.failed_at == now
    assert result.next_attempt_at is None


def test_delivery_error_classification() -> None:
    assert (
        delivery_task_module._is_permanent_delivery_error(
            PlatformTransportError("transport", platform="telegram")
        )
        is False
    )
    assert (
        delivery_task_module._is_permanent_delivery_error(
            PlatformHTTPError("rate", platform="telegram", status_code=429)
        )
        is False
    )
    assert (
        delivery_task_module._is_permanent_delivery_error(
            PlatformHTTPError("server", platform="telegram", status_code=503)
        )
        is False
    )
    assert (
        delivery_task_module._is_permanent_delivery_error(
            PlatformHTTPError("bad", platform="telegram", status_code=400)
        )
        is True
    )


def test_celery_beat_schedules_outbound_recovery() -> None:
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule["recover-outbound-deliveries"]
    assert schedule["task"] == "recover_outbound_deliveries"
    assert schedule["schedule"] > 0


@pytest.mark.asyncio
async def test_conversation_service_commits_outbox_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    event_id = uuid.uuid4()
    conversation = _conversation()
    client = SimpleNamespace(display_name="Соня", username=None, is_vip=False)
    outbound = SimpleNamespace(id=22)
    delivery = _delivery(message_id=22)
    db = SimpleNamespace(commit=AsyncMock(side_effect=lambda: calls.append("commit")))
    message_repo = SimpleNamespace(
        get_by_causation=AsyncMock(return_value=None),
        get_history=AsyncMock(return_value=[]),
        create_message_once=AsyncMock(return_value=(outbound, True)),
    )
    conversation_repo = SimpleNamespace(
        increment_ai_messages=AsyncMock(),
        escalate=AsyncMock(),
    )
    outbox_repo = SimpleNamespace(
        create_for_message=AsyncMock(return_value=(delivery, True)),
    )
    adapter = SimpleNamespace(send_message=AsyncMock())
    ai_client = SimpleNamespace(generate_response=AsyncMock(return_value="Ответ"))
    service = ConversationService(
        db=db,
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=conversation_repo,
        message_repo=message_repo,
        platform_adapter=adapter,
        ai_client=ai_client,
        outbound_delivery_repo=outbox_repo,
    )
    service._resolve_conversation = AsyncMock(return_value=(client, conversation))

    monkeypatch.setattr(
        conversation_service_module.IntentClassifier,
        "classify",
        lambda _text: "pricing",
    )
    monkeypatch.setattr(
        conversation_service_module.EscalationEngine,
        "should_escalate",
        lambda _intent, _text: (False, ""),
    )
    monkeypatch.setattr(
        conversation_service_module.deliver_outbound_message_task,
        "delay",
        MagicMock(side_effect=lambda _delivery_id: calls.append("dispatch")),
    )

    message = SimpleNamespace(
        text="Сколько стоит?",
        platform="telegram",
        chat_id="123",
        message_id="5",
        user=SimpleNamespace(external_id="123", display_name="Соня", username=None),
    )
    await service.process_message(message, causation_event_id=event_id)

    assert calls == ["commit", "dispatch"]
    outbox_repo.create_for_message.assert_awaited_once_with(
        message_id=22,
        platform="telegram",
        destination_id="123",
    )
    adapter.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_conversation_suppresses_ai_and_outbound() -> None:
    conversation = _conversation(status=CONVERSATION_ESCALATED)
    service = ConversationService(
        db=SimpleNamespace(commit=AsyncMock()),
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=SimpleNamespace(),
        message_repo=SimpleNamespace(get_by_causation=AsyncMock(return_value=None)),
        platform_adapter=SimpleNamespace(send_message=AsyncMock()),
        ai_client=SimpleNamespace(generate_response=AsyncMock()),
        outbound_delivery_repo=SimpleNamespace(),
    )
    service._resolve_conversation = AsyncMock(return_value=(SimpleNamespace(), conversation))
    message = SimpleNamespace(
        text="Есть ещё вопрос",
        platform="telegram",
        chat_id="123",
        message_id="6",
        user=SimpleNamespace(external_id="123", display_name="Соня", username=None),
    )

    await service.process_message(message, causation_event_id=uuid.uuid4())

    service.ai_client.generate_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversation_state_machine_transitions() -> None:
    session = SimpleNamespace(flush=AsyncMock())
    repo = ConversationRepository(session)
    conversation = _conversation(status=CONVERSATION_ESCALATED)
    repo.get_locked = AsyncMock(return_value=conversation)

    taken = await repo.take_over(conversation.id)
    assert taken.status == CONVERSATION_HUMAN_OWNED
    assert taken.assigned_to_human is True

    returned = await repo.return_to_ai(conversation.id)
    assert returned.status == CONVERSATION_ACTIVE
    assert returned.assigned_to_human is False


@pytest.mark.asyncio
async def test_invalid_handoff_transition_is_rejected() -> None:
    session = SimpleNamespace(flush=AsyncMock())
    repo = ConversationRepository(session)
    conversation = _conversation(status="closed")
    repo.get_locked = AsyncMock(return_value=conversation)

    with pytest.raises(InvalidConversationTransitionError):
        await repo.return_to_ai(conversation.id)


class AsyncSessionContext:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_existing_business_response_rebuilds_missing_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_id = uuid.uuid4()
    outbound = SimpleNamespace(id=8, platform_message_id=None)
    delivery = _delivery(message_id=8)
    outbox_repo = SimpleNamespace(
        get_by_message_id=AsyncMock(return_value=None),
        create_for_message=AsyncMock(return_value=(delivery, True)),
    )
    service = ConversationService(
        db=SimpleNamespace(commit=AsyncMock()),
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=SimpleNamespace(),
        message_repo=SimpleNamespace(get_by_causation=AsyncMock(return_value=outbound)),
        platform_adapter=SimpleNamespace(send_message=AsyncMock()),
        ai_client=SimpleNamespace(generate_response=AsyncMock()),
        outbound_delivery_repo=outbox_repo,
    )
    dispatch = MagicMock()
    monkeypatch.setattr(
        conversation_service_module.deliver_outbound_message_task,
        "delay",
        dispatch,
    )
    message = SimpleNamespace(
        text="Привет",
        platform="telegram",
        chat_id="123",
        message_id="5",
        user=SimpleNamespace(external_id="123", display_name="Соня", username=None),
    )

    await service.process_message(message, causation_event_id=event_id)

    outbox_repo.create_for_message.assert_awaited_once_with(
        message_id=8,
        platform="telegram",
        destination_id="123",
    )
    dispatch.assert_called_once_with(str(delivery.id))
    service.ai_client.generate_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_worker_marks_success_and_platform_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _delivery(status=OUTBOUND_DELIVERY_SENDING, attempts=1)
    message = SimpleNamespace(id=10, content="Ответ", platform_message_id=None)
    claim_session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    final_session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    sessions = iter([claim_session, final_session])

    class DeliveryRepository:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def claim(self, *_args: Any, **_kwargs: Any) -> Any:
            return delivery

        async def mark_sent(self, *_args: Any, **_kwargs: Any) -> Any:
            return delivery

    class Messages:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_by_id(self, _message_id: int) -> Any:
            return message

    monkeypatch.setattr(
        delivery_task_module,
        "async_session_factory",
        lambda: AsyncSessionContext(next(sessions)),
    )
    monkeypatch.setattr(
        delivery_task_module,
        "OutboundDeliveryRepository",
        DeliveryRepository,
    )
    monkeypatch.setattr(delivery_task_module, "MessageRepository", Messages)
    monkeypatch.setattr(
        delivery_task_module,
        "_send_to_platform",
        AsyncMock(return_value="telegram-99"),
    )

    outcome = await delivery_task_module._deliver_outbound(delivery.id)

    assert outcome.status == "sent"
    assert message.platform_message_id == "telegram-99"
    claim_session.commit.assert_awaited_once()
    final_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_worker_persists_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _delivery(status=OUTBOUND_DELIVERY_SENDING, attempts=2)
    message = SimpleNamespace(id=10, content="Ответ", platform_message_id=None)
    claim_session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    failure_session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    sessions = iter([claim_session, failure_session])
    failure_calls: list[dict[str, Any]] = []

    class DeliveryRepository:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def claim(self, *_args: Any, **_kwargs: Any) -> Any:
            return delivery

        async def mark_failure(self, *_args: Any, **kwargs: Any) -> Any:
            failure_calls.append(kwargs)
            return SimpleNamespace(status=OUTBOUND_DELIVERY_RETRYING)

    class Messages:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_by_id(self, _message_id: int) -> Any:
            return message

    monkeypatch.setattr(
        delivery_task_module,
        "async_session_factory",
        lambda: AsyncSessionContext(next(sessions)),
    )
    monkeypatch.setattr(
        delivery_task_module,
        "OutboundDeliveryRepository",
        DeliveryRepository,
    )
    monkeypatch.setattr(delivery_task_module, "MessageRepository", Messages)
    monkeypatch.setattr(
        delivery_task_module,
        "_send_to_platform",
        AsyncMock(
            side_effect=PlatformHTTPError(
                "temporary",
                platform="telegram",
                status_code=503,
            )
        ),
    )

    outcome = await delivery_task_module._deliver_outbound(delivery.id)

    assert outcome.status == OUTBOUND_DELIVERY_RETRYING
    assert failure_calls[0]["permanent"] is False
    assert failure_calls[0]["error_code"] == "PlatformHTTP503"
    failure_session.commit.assert_awaited_once()
