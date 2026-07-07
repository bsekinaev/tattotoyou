"""PostgreSQL integration tests for Transactional Outbox and handoff."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.clients.models import Platform
from app.domain.conversations.models import (
    CONVERSATION_ACTIVE,
    CONVERSATION_HUMAN_OWNED,
    Conversation,
)
from app.domain.outbound.models import OUTBOUND_DELIVERY_SENDING, OutboundDelivery
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    MessageRepository,
    OutboundDeliveryRepository,
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
        table = await connection.scalar(text("SELECT to_regclass('public.outbound_deliveries')"))
        open_index = await connection.scalar(
            text("SELECT to_regclass('public.uq_conversations_one_active_per_client')")
        )
        sender_column = await connection.scalar(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'messages'
                  AND column_name = 'sender_type'
                """
            )
        )
        notification_column = await connection.scalar(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'outbound_deliveries'
                  AND column_name = 'deduplication_key'
                """
            )
        )
        if (
            table is None
            or open_index is None
            or sender_column is None
            or notification_column is None
        ):
            pytest.fail(
                "PostgreSQL test schema does not contain the Outbox revision. "
                "Run `python -m alembic upgrade head` first."
            )


@pytest.mark.asyncio
async def test_outbound_delivery_is_created_once_and_claimed_once() -> None:
    engine = create_async_engine(
        _test_dsn(),
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    platform_name = f"outbox-{suffix[:12]}"
    platform_id: int | None = None

    try:
        await _assert_schema_ready(engine)
        async with session_factory() as session, session.begin():
            platform = await PlatformRepository(session).get_or_create(name=platform_name)
            platform_id = platform.id
            client = await ClientRepository(session).get_or_create(
                platform_id=platform.id,
                external_id=f"client-{suffix}",
                display_name="Outbox Integration",
            )
            conversation = await ConversationRepository(session).get_or_create_active(client.id)
            message = await MessageRepository(session).create_message(
                conversation_id=conversation.id,
                direction="outbound",
                sender_type="bot",
                content="Надёжный ответ",
            )
            message_id = message.id

        async def create_delivery() -> uuid.UUID:
            async with session_factory() as session, session.begin():
                delivery, _created = await OutboundDeliveryRepository(session).create_for_message(
                    message_id=message_id,
                    platform="telegram",
                    destination_id="123",
                )
                return delivery.id

        delivery_ids = await asyncio.gather(*(create_delivery() for _ in range(20)))
        assert len(set(delivery_ids)) == 1
        delivery_id = delivery_ids[0]

        async def claim_delivery() -> uuid.UUID | None:
            async with session_factory() as session, session.begin():
                delivery = await OutboundDeliveryRepository(session).claim(
                    delivery_id,
                    processing_timeout=timedelta(minutes=5),
                )
                return delivery.id if delivery else None

        claimed_ids = await asyncio.gather(*(claim_delivery() for _ in range(20)))
        assert [claimed for claimed in claimed_ids if claimed is not None] == [delivery_id]

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(OutboundDelivery)
                .where(OutboundDelivery.message_id == message_id)
            )
            delivery = await session.get(OutboundDelivery, delivery_id)

        assert count == 1
        assert delivery is not None
        assert delivery.status == OUTBOUND_DELIVERY_SENDING
        assert delivery.attempts == 1
    finally:
        try:
            if platform_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(Platform).where(Platform.id == platform_id))
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_notification_delivery_is_deduplicated_under_concurrency() -> None:
    engine = create_async_engine(
        _test_dsn(),
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    key = f"integration-notification-{uuid.uuid4().hex}"

    try:
        await _assert_schema_ready(engine)

        async def create_notification() -> uuid.UUID:
            async with session_factory() as session, session.begin():
                delivery, _created = await OutboundDeliveryRepository(session).create_notification(
                    platform="telegram",
                    destination_id="777",
                    payload_text="Надёжное уведомление",
                    deduplication_key=key,
                )
                return delivery.id

        delivery_ids = await asyncio.gather(*(create_notification() for _ in range(20)))
        assert len(set(delivery_ids)) == 1

        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(OutboundDelivery)
                .where(OutboundDelivery.deduplication_key == key)
            )
            delivery = await session.get(OutboundDelivery, delivery_ids[0])

        assert count == 1
        assert delivery is not None
        assert delivery.message_id is None
        assert delivery.payload_text == "Надёжное уведомление"
    finally:
        try:
            async with session_factory() as session, session.begin():
                await session.execute(
                    delete(OutboundDelivery).where(OutboundDelivery.deduplication_key == key)
                )
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_handoff_state_machine_preserves_one_open_conversation() -> None:
    engine = create_async_engine(_test_dsn(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    platform_name = f"handoff-{suffix[:12]}"
    platform_id: int | None = None

    try:
        await _assert_schema_ready(engine)
        async with session_factory() as session, session.begin():
            platform = await PlatformRepository(session).get_or_create(name=platform_name)
            platform_id = platform.id
            client = await ClientRepository(session).get_or_create(
                platform_id=platform.id,
                external_id=f"client-{suffix}",
                display_name="Handoff Integration",
            )
            repo = ConversationRepository(session)
            conversation = await repo.get_or_create_active(client.id)
            await repo.escalate(conversation)
            conversation_id = conversation.id

        async with session_factory() as session, session.begin():
            repo = ConversationRepository(session)
            conversation = await repo.take_over(conversation_id)
            assert conversation.status == CONVERSATION_HUMAN_OWNED

        async with session_factory() as session, session.begin():
            repo = ConversationRepository(session)
            conversation = await repo.return_to_ai(conversation_id)
            assert conversation.status == CONVERSATION_ACTIVE

        async with session_factory() as session:
            open_count = await session.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(
                    Conversation.client_id == client.id,
                    Conversation.status.in_(("active", "escalated", "human_owned")),
                )
            )
        assert open_count == 1
    finally:
        try:
            if platform_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(Platform).where(Platform.id == platform_id))
        finally:
            await engine.dispose()
