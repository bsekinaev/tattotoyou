"""PostgreSQL integration test продуктового ответа из панели Сони."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.clients.models import Platform
from app.domain.conversations.models import CONVERSATION_HUMAN_OWNED, Message
from app.domain.outbound.models import OUTBOUND_DELIVERY_PENDING, OutboundDelivery
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    PlatformRepository,
)
from app.services import studio_dashboard as dashboard_module
from app.services.studio_dashboard import StudioDashboardService

pytestmark = pytest.mark.integration


def _test_dsn() -> str:
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


@pytest.mark.asyncio
async def test_human_reply_is_saved_with_outbox_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(_test_dsn(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    platform_name = f"studio-{suffix[:12]}"
    platform_id: int | None = None
    dispatched: list[str] = []

    monkeypatch.setattr(
        dashboard_module.deliver_outbound_message_task,
        "delay",
        lambda delivery_id: dispatched.append(delivery_id),
    )

    try:
        async with engine.connect() as connection:
            lead_column = await connection.scalar(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'clients'
                      AND column_name = 'lead_status'
                    """
                )
            )
            if lead_column is None:
                pytest.fail(
                    "PostgreSQL schema does not contain the studio dashboard revision. "
                    "Run `python -m alembic upgrade head` first."
                )

        async with session_factory() as session, session.begin():
            platform = await PlatformRepository(session).get_or_create(name=platform_name)
            platform_id = platform.id
            client = await ClientRepository(session).get_or_create(
                platform_id=platform.id,
                external_id=f"client-{suffix}",
                display_name="Studio Integration",
            )
            conversation = await ConversationRepository(session).get_or_create_active(client.id)
            conversation_id = conversation.id

        async with session_factory() as session:
            delivery = await StudioDashboardService(session).send_human_reply(
                conversation_id,
                "Ручной ответ Сони",
            )
            delivery_id = delivery.id

        async with session_factory() as session:
            conversation = await session.get(
                dashboard_module.Conversation,
                conversation_id,
            )
            message = await session.scalar(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.sender_type == "human",
                )
            )
            saved_delivery = await session.get(OutboundDelivery, delivery_id)

        assert conversation is not None
        assert conversation.status == CONVERSATION_HUMAN_OWNED
        assert conversation.human_messages_count == 1
        assert message is not None
        assert message.content == "Ручной ответ Сони"
        assert saved_delivery is not None
        assert saved_delivery.status == OUTBOUND_DELIVERY_PENDING
        assert dispatched == [str(delivery_id)]
    finally:
        try:
            if platform_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(Platform).where(Platform.id == platform_id))
        finally:
            await engine.dispose()
