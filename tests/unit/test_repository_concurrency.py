"""Regression tests for concurrency-safe repository operations."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.domain.clients.models import Client, Platform
from app.domain.conversations.models import Conversation
from app.infrastructure.db.repositories.client_repository import ClientRepository
from app.infrastructure.db.repositories.conversation_repository import ConversationRepository
from app.infrastructure.db.repositories.platform_repository import PlatformRepository


class ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class Scalars:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def all(self) -> list[Any]:
        return self.values


class CollectionResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def scalars(self) -> Scalars:
        return Scalars(self.values)


class FakeSession:
    def __init__(self, results: list[Any]) -> None:
        self.execute = AsyncMock(side_effect=results)
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.added: list[Any] = []

    def add(self, value: Any) -> None:
        self.added.append(value)


@pytest.mark.asyncio
async def test_platform_get_or_create_uses_postgresql_upsert() -> None:
    existing = Platform(id=7, name="telegram", is_active=True)
    session = FakeSession([ScalarResult(None), ScalarResult(existing)])

    result = await PlatformRepository(session).get_or_create(name="telegram")

    assert result is existing
    insert_statement = session.execute.await_args_list[0].args[0]
    sql = str(insert_statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (name) DO NOTHING" in sql
    assert "RETURNING platforms.id" in sql
    assert insert_statement.compile(dialect=postgresql.dialect()).params["is_active"] is True


@pytest.mark.asyncio
async def test_client_get_or_create_uses_composite_postgresql_upsert() -> None:
    existing = Client(
        id=11,
        platform_id=7,
        external_id="123",
        display_name="Клиент",
        is_vip=False,
        is_banned=False,
    )
    session = FakeSession([ScalarResult(None), ScalarResult(existing)])

    result = await ClientRepository(session).get_or_create(
        platform_id=7,
        external_id="123",
        display_name="Клиент",
    )

    assert result is existing
    insert_statement = session.execute.await_args_list[0].args[0]
    sql = str(insert_statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (platform_id, external_id) DO NOTHING" in sql
    assert "RETURNING clients.id" in sql
    params = insert_statement.compile(dialect=postgresql.dialect()).params
    assert params["is_vip"] is False
    assert params["is_banned"] is False


@pytest.mark.asyncio
async def test_active_conversation_creation_locks_client_row() -> None:
    conversation = Conversation(
        client_id=3,
        status="active",
        last_activity_at=datetime.now(UTC),
    )
    session = FakeSession([ScalarResult(3), CollectionResult([conversation])])

    result = await ConversationRepository(session).get_or_create_active(client_id=3)

    assert result is conversation
    lock_statement = session.execute.await_args_list[0].args[0]
    sql = str(lock_statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_active_conversation_is_closed_before_replacement() -> None:
    stale = Conversation(
        client_id=3,
        status="active",
        last_activity_at=datetime.now(UTC) - timedelta(hours=25),
    )
    session = FakeSession([ScalarResult(3), CollectionResult([stale])])

    result = await ConversationRepository(session).get_or_create_active(client_id=3)

    assert stale.status == "closed"
    assert stale.closed_at is not None
    assert result is session.added[0]
    assert result.status == "active"
    assert result.client_id == 3
    assert session.flush.await_count == 2
    session.refresh.assert_awaited_once_with(result)


@pytest.mark.asyncio
async def test_duplicate_active_conversations_are_self_healed() -> None:
    now = datetime.now(UTC)
    current = Conversation(client_id=3, status="active", last_activity_at=now)
    duplicate = Conversation(
        client_id=3,
        status="active",
        last_activity_at=now - timedelta(minutes=1),
    )
    session = FakeSession([ScalarResult(3), CollectionResult([current, duplicate])])

    result = await ConversationRepository(session).get_or_create_active(client_id=3)

    assert result is current
    assert current.status == "active"
    assert duplicate.status == "closed"
    assert duplicate.closed_at is not None
    session.flush.assert_awaited_once()
