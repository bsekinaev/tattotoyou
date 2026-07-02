"""Регрессионные тесты PostgreSQL Inbox входящих событий."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.api.webhooks import telegram as telegram_webhook_module
from app.domain.conversations.models import Message
from app.domain.incoming.models import (
    INCOMING_EVENT_FAILED,
    INCOMING_EVENT_PENDING,
    INCOMING_EVENT_PROCESSING,
    INCOMING_EVENT_RETRYING,
    IncomingEvent,
)
from app.infrastructure.db.repositories.incoming_event_repository import (
    IncomingEventRepository,
)
from app.infrastructure.db.repositories.message_repository import MessageRepository
from app.services.conversation_service import ConversationService
from app.services.platforms.telegram.schemas import TelegramUpdate


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


class AsyncSessionContext:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


def _event(**overrides: Any) -> IncomingEvent:
    values = {
        "id": uuid.uuid4(),
        "platform": "telegram",
        "external_event_id": "100",
        "payload": {"update_id": 100},
        "status": INCOMING_EVENT_PENDING,
        "attempts": 0,
    }
    values.update(overrides)
    return IncomingEvent(**values)


@pytest.mark.asyncio
async def test_incoming_event_get_or_create_uses_composite_upsert() -> None:
    existing = _event()
    session = FakeSession([ScalarResult(None), ScalarResult(existing)])

    event, created = await IncomingEventRepository(session).get_or_create(
        platform="telegram",
        external_event_id="100",
        payload={"update_id": 100},
    )

    assert event is existing
    assert created is False
    statement = session.execute.await_args_list[0].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ON CONFLICT (platform, external_event_id) DO NOTHING" in sql
    assert "RETURNING incoming_events.id" in sql
    assert compiled.params["status"] == INCOMING_EVENT_PENDING


@pytest.mark.asyncio
async def test_claim_uses_skip_locked_and_marks_processing() -> None:
    event = _event()
    session = FakeSession([ScalarResult(event)])
    now = datetime.now(UTC)

    claimed = await IncomingEventRepository(session).claim(
        event.id,
        processing_timeout=timedelta(minutes=5),
        now=now,
    )

    assert claimed is event
    assert event.status == INCOMING_EVENT_PROCESSING
    assert event.attempts == 1
    assert event.processing_started_at == now
    statement = session.execute.await_args_list[0].args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "incoming_events.status IN" in sql
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_failure_moves_event_to_retrying_then_failed() -> None:
    now = datetime.now(UTC)
    retrying = _event(status=INCOMING_EVENT_PROCESSING, attempts=2)
    retry_session = FakeSession([ScalarResult(retrying)])

    result = await IncomingEventRepository(retry_session).mark_failure(
        retrying.id,
        error_code="TimeoutError",
        max_attempts=3,
        retry_delay=timedelta(seconds=30),
        now=now,
    )

    assert result.status == INCOMING_EVENT_RETRYING
    assert result.next_attempt_at == now + timedelta(seconds=30)
    assert result.last_error_code == "TimeoutError"

    failed = _event(status=INCOMING_EVENT_PROCESSING, attempts=3)
    failed_session = FakeSession([ScalarResult(failed)])
    result = await IncomingEventRepository(failed_session).mark_failure(
        failed.id,
        error_code="RuntimeError",
        max_attempts=3,
        retry_delay=timedelta(seconds=30),
        now=now,
    )

    assert result.status == INCOMING_EVENT_FAILED
    assert result.next_attempt_at is None


@pytest.mark.asyncio
async def test_message_effect_uses_partial_idempotency_index() -> None:
    event_id = uuid.uuid4()
    existing = Message(
        id=5,
        conversation_id=uuid.uuid4(),
        direction="outbound",
        content="Ответ",
        causation_event_id=event_id,
        is_escalation_trigger=False,
    )
    session = FakeSession([ScalarResult(None), ScalarResult(existing)])

    message, created = await MessageRepository(session).create_message_once(
        conversation_id=existing.conversation_id,
        direction="outbound",
        content="Ответ",
        causation_event_id=event_id,
    )

    assert message is existing
    assert created is False
    statement = session.execute.await_args_list[0].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "ON CONFLICT (causation_event_id, direction)" in sql
    assert "WHERE causation_event_id IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_webhook_commits_inbox_before_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    event = _event()

    class Session:
        async def commit(self) -> None:
            calls.append("commit")

    class Repository:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_or_create(self, **_kwargs: Any) -> tuple[IncomingEvent, bool]:
            calls.append("persist")
            return event, True

    class Task:
        @staticmethod
        def delay(event_id: str) -> None:
            assert event_id == str(event.id)
            calls.append("dispatch")

    monkeypatch.setattr(
        telegram_webhook_module,
        "async_session_factory",
        lambda: AsyncSessionContext(Session()),
    )
    monkeypatch.setattr(telegram_webhook_module, "IncomingEventRepository", Repository)
    monkeypatch.setattr(telegram_webhook_module, "process_telegram_update_task", Task())

    update = TelegramUpdate.model_validate({"update_id": 100})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=AsyncMock())))
    secret = telegram_webhook_module.settings.telegram_webhook_secret.get_secret_value()

    response = await telegram_webhook_module.telegram_webhook(
        request,
        update,
        secret,
    )

    assert response == {"status": "accepted", "dispatched": True}
    assert calls == ["persist", "commit", "dispatch"]


@pytest.mark.asyncio
async def test_webhook_keeps_committed_event_when_broker_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event()
    session = SimpleNamespace(commit=AsyncMock())

    class Repository:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_or_create(self, **_kwargs: Any) -> tuple[IncomingEvent, bool]:
            return event, True

    task = SimpleNamespace(delay=MagicMock(side_effect=RuntimeError("broker down")))
    monkeypatch.setattr(
        telegram_webhook_module,
        "async_session_factory",
        lambda: AsyncSessionContext(session),
    )
    monkeypatch.setattr(telegram_webhook_module, "IncomingEventRepository", Repository)
    monkeypatch.setattr(telegram_webhook_module, "process_telegram_update_task", task)

    update = TelegramUpdate.model_validate({"update_id": 100})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=AsyncMock())))
    secret = telegram_webhook_module.settings.telegram_webhook_secret.get_secret_value()

    response = await telegram_webhook_module.telegram_webhook(request, update, secret)

    assert response == {"status": "accepted", "dispatched": False}
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_outbound_effect_short_circuits_conversation_processing() -> None:
    event_id = uuid.uuid4()
    existing_outbound = SimpleNamespace(id=8)
    message_repo = SimpleNamespace(get_by_causation=AsyncMock(return_value=existing_outbound))
    platform_adapter = SimpleNamespace(send_message=AsyncMock())
    ai_client = SimpleNamespace(generate_response=AsyncMock())
    service = ConversationService(
        db=SimpleNamespace(commit=AsyncMock()),
        platform_repo=SimpleNamespace(),
        client_repo=SimpleNamespace(),
        conversation_repo=SimpleNamespace(),
        message_repo=message_repo,
        platform_adapter=platform_adapter,
        ai_client=ai_client,
    )
    platform_message = SimpleNamespace(
        text="Привет",
        platform="telegram",
        chat_id="1",
        message_id="10",
        user=SimpleNamespace(external_id="2", display_name="Соня", username=None),
    )

    await service.process_message(
        platform_message,
        causation_event_id=event_id,
    )

    ai_client.generate_response.assert_not_awaited()
    platform_adapter.send_message.assert_not_awaited()


def test_celery_beat_schedules_incoming_event_recovery() -> None:
    from app.workers.celery_app import celery_app

    schedule = celery_app.conf.beat_schedule["recover-incoming-events"]
    assert schedule["task"] == "recover_incoming_events"
    assert schedule["schedule"] > 0


def test_recovery_dispatches_every_due_event(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers.tasks import process_telegram_update as worker_module

    event_ids = [uuid.uuid4(), uuid.uuid4()]
    monkeypatch.setattr(
        worker_module,
        "_list_recoverable_event_ids",
        AsyncMock(return_value=event_ids),
    )
    delay = MagicMock()
    monkeypatch.setattr(worker_module.process_telegram_update_task, "delay", delay)

    result = worker_module.recover_incoming_events_task.run()

    assert result == {"found": 2, "dispatched": 2}
    assert [call.args[0] for call in delay.call_args_list] == [
        str(event_ids[0]),
        str(event_ids[1]),
    ]


def test_retry_delay_uses_bounded_exponential_backoff() -> None:
    from app.workers.tasks import process_telegram_update as worker_module

    base = worker_module.settings.incoming_event_retry_base_seconds
    assert worker_module._retry_delay_seconds(1) == base
    assert worker_module._retry_delay_seconds(2) == base * 2
    assert worker_module._retry_delay_seconds(100) == 3600


@pytest.mark.asyncio
async def test_worker_marks_claimed_event_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers.tasks import process_telegram_update as worker_module

    event = _event(status=INCOMING_EVENT_PROCESSING, attempts=1)
    commits: list[AsyncMock] = []
    processed: list[uuid.UUID] = []

    class Session:
        def __init__(self) -> None:
            self.commit = AsyncMock()
            self.rollback = AsyncMock()
            commits.append(self.commit)

    class Repository:
        def __init__(self, _session: Any) -> None:
            pass

        async def claim(self, *_args: Any, **_kwargs: Any) -> IncomingEvent:
            return event

        async def mark_processed(self, event_id: uuid.UUID) -> IncomingEvent:
            processed.append(event_id)
            event.status = "processed"
            return event

    monkeypatch.setattr(
        worker_module,
        "async_session_factory",
        lambda: AsyncSessionContext(Session()),
    )
    monkeypatch.setattr(worker_module, "IncomingEventRepository", Repository)
    process = AsyncMock()
    monkeypatch.setattr(worker_module, "_process_async", process)

    outcome = await worker_module._process_incoming_event(event.id)

    assert outcome.status == "processed"
    assert processed == [event.id]
    process.assert_awaited_once_with(event.payload, causation_event_id=event.id)
    assert len(commits) == 2
    assert all(commit.await_count == 1 for commit in commits)


@pytest.mark.asyncio
async def test_worker_persists_retry_state_after_processing_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.tasks import process_telegram_update as worker_module

    event = _event(status=INCOMING_EVENT_PROCESSING, attempts=2)
    failures: list[dict[str, Any]] = []

    class Session:
        commit = AsyncMock()
        rollback = AsyncMock()

    class Repository:
        def __init__(self, _session: Any) -> None:
            pass

        async def claim(self, *_args: Any, **_kwargs: Any) -> IncomingEvent:
            return event

        async def mark_failure(self, _event_id: uuid.UUID, **kwargs: Any) -> IncomingEvent:
            failures.append(kwargs)
            event.status = INCOMING_EVENT_RETRYING
            return event

    monkeypatch.setattr(
        worker_module,
        "async_session_factory",
        lambda: AsyncSessionContext(Session()),
    )
    monkeypatch.setattr(worker_module, "IncomingEventRepository", Repository)
    monkeypatch.setattr(
        worker_module,
        "_process_async",
        AsyncMock(side_effect=TimeoutError("temporary")),
    )

    outcome = await worker_module._process_incoming_event(event.id)

    assert outcome.status == INCOMING_EVENT_RETRYING
    assert isinstance(outcome.error, TimeoutError)
    assert failures[0]["error_code"] == "TimeoutError"
    assert failures[0]["max_attempts"] == worker_module.settings.incoming_event_max_attempts
    assert failures[0]["retry_delay"].total_seconds() > 0
