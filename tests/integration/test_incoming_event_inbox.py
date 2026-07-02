"""PostgreSQL integration tests for the durable incoming-event Inbox."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.clients.models import Platform
from app.domain.conversations.models import Message
from app.domain.incoming.models import INCOMING_EVENT_PROCESSING, IncomingEvent
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    IncomingEventRepository,
    MessageRepository,
    PlatformRepository,
)

pytestmark = pytest.mark.integration


def _test_dsn() -> str:
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


async def _assert_schema_ready(engine) -> None:
    async with engine.connect() as connection:
        incoming_table = await connection.scalar(
            text("SELECT to_regclass('public.incoming_events')")
        )
        causation_index = await connection.scalar(
            text("SELECT to_regclass('public.uq_messages_causation_direction')")
        )
        if incoming_table is None or causation_index is None:
            pytest.fail(
                "PostgreSQL test schema does not contain the incoming-event Inbox. "
                "Run `python -m alembic upgrade head` first."
            )


@pytest.mark.asyncio
async def test_incoming_event_is_created_once_and_claimed_once() -> None:
    engine = create_async_engine(
        _test_dsn(),
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    external_event_id = f"integration-{suffix}"
    event_id: uuid.UUID | None = None

    async def get_event_id() -> uuid.UUID:
        async with session_factory() as session, session.begin():
            event, _created = await IncomingEventRepository(session).get_or_create(
                platform="telegram",
                external_event_id=external_event_id,
                payload={"update_id": external_event_id},
            )
            return event.id

    async def claim_event(candidate_id: uuid.UUID) -> uuid.UUID | None:
        async with session_factory() as session, session.begin():
            event = await IncomingEventRepository(session).claim(
                candidate_id,
                processing_timeout=timedelta(minutes=5),
            )
            return event.id if event else None

    try:
        await _assert_schema_ready(engine)
        event_ids = await asyncio.gather(*(get_event_id() for _ in range(20)))
        assert len(set(event_ids)) == 1
        event_id = event_ids[0]

        claimed_ids = await asyncio.gather(*(claim_event(event_id) for _ in range(20)))
        assert [claimed for claimed in claimed_ids if claimed is not None] == [event_id]

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(IncomingEvent)
                .where(
                    IncomingEvent.platform == "telegram",
                    IncomingEvent.external_event_id == external_event_id,
                )
            )
            event = await session.get(IncomingEvent, event_id)

        assert count == 1
        assert event is not None
        assert event.status == INCOMING_EVENT_PROCESSING
        assert event.attempts == 1
    finally:
        try:
            if event_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(IncomingEvent).where(IncomingEvent.id == event_id))
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_message_business_effect_is_created_once_under_concurrency() -> None:
    engine = create_async_engine(
        _test_dsn(),
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    platform_name = f"inbox-{suffix[:12]}"
    event_id: uuid.UUID | None = None
    platform_id: int | None = None

    try:
        await _assert_schema_ready(engine)
        async with session_factory() as session, session.begin():
            event, _created = await IncomingEventRepository(session).get_or_create(
                platform="telegram",
                external_event_id=f"message-{suffix}",
                payload={"update_id": suffix},
            )
            event_id = event.id
            platform = await PlatformRepository(session).get_or_create(name=platform_name)
            platform_id = platform.id
            client = await ClientRepository(session).get_or_create(
                platform_id=platform.id,
                external_id=f"client-{suffix}",
                display_name="Inbox Integration",
            )
            conversation = await ConversationRepository(session).get_or_create_active(client.id)
            conversation_id = conversation.id

        async def create_effect() -> int:
            async with session_factory() as session, session.begin():
                message, _created = await MessageRepository(session).create_message_once(
                    conversation_id=conversation_id,
                    direction="inbound",
                    content="Один входящий эффект",
                    platform_message_id="42",
                    causation_event_id=event_id,
                )
                return message.id

        message_ids = await asyncio.gather(*(create_effect() for _ in range(20)))
        assert len(set(message_ids)) == 1

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.causation_event_id == event_id,
                    Message.direction == "inbound",
                )
            )
        assert count == 1
    finally:
        try:
            if platform_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(Platform).where(Platform.id == platform_id))
            if event_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(IncomingEvent).where(IncomingEvent.id == event_id))
        finally:
            await engine.dispose()
