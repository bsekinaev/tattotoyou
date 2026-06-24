"""PostgreSQL concurrency tests for repository invariants.

Run explicitly against a migrated disposable/test database:

    $env:TEST_POSTGRES_DSN = python -c "from app.core.config import get_settings; print(get_settings().postgres_dsn)"
    python -m pytest tests/integration/test_repository_concurrency.py -v
"""

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.clients.models import Client, Platform
from app.domain.conversations.models import Conversation
from app.infrastructure.db.repositories.client_repository import ClientRepository
from app.infrastructure.db.repositories.conversation_repository import ConversationRepository
from app.infrastructure.db.repositories.platform_repository import PlatformRepository

pytestmark = pytest.mark.integration


def _test_dsn() -> str:
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


async def _assert_schema_ready(engine) -> None:
    """Fail with an actionable message when the target DB is not migrated."""
    async with engine.connect() as connection:
        alembic_table = await connection.scalar(
            text("SELECT to_regclass('public.alembic_version')")
        )
        platforms_table = await connection.scalar(text("SELECT to_regclass('public.platforms')"))
        active_conversation_index = await connection.scalar(
            text("SELECT to_regclass('public.uq_conversations_one_active_per_client')")
        )

        if alembic_table is None or platforms_table is None or active_conversation_index is None:
            pytest.fail(
                "PostgreSQL test schema is not migrated to the concurrency-safe revision. "
                "Run `python -m alembic upgrade head` before integration tests."
            )


@pytest.mark.asyncio
async def test_get_or_create_is_safe_under_concurrency() -> None:
    engine = create_async_engine(
        _test_dsn(),
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    schema_ready = False
    suffix = uuid.uuid4().hex
    platform_name = f"test-{suffix[:12]}"
    external_id = f"client-{suffix}"

    async def get_platform_id() -> int:
        async with session_factory() as session, session.begin():
            platform = await PlatformRepository(session).get_or_create(
                name=platform_name,
                is_active=True,
            )
            return platform.id

    async def get_client_id(platform_id: int) -> int:
        async with session_factory() as session, session.begin():
            client = await ClientRepository(session).get_or_create(
                platform_id=platform_id,
                external_id=external_id,
                display_name="Concurrency Test",
                is_vip=False,
                is_banned=False,
            )
            return client.id

    async def get_conversation_id(client_id: int) -> uuid.UUID:
        async with session_factory() as session, session.begin():
            conversation = await ConversationRepository(session).get_or_create_active(client_id)
            return conversation.id

    try:
        await _assert_schema_ready(engine)
        schema_ready = True

        platform_ids = await asyncio.gather(*(get_platform_id() for _ in range(20)))
        assert len(set(platform_ids)) == 1
        platform_id = platform_ids[0]

        client_ids = await asyncio.gather(*(get_client_id(platform_id) for _ in range(20)))
        assert len(set(client_ids)) == 1
        client_id = client_ids[0]

        conversation_ids = await asyncio.gather(
            *(get_conversation_id(client_id) for _ in range(20))
        )
        assert len(set(conversation_ids)) == 1

        async with session_factory() as session:
            platform_count = await session.scalar(
                select(func.count()).select_from(Platform).where(Platform.name == platform_name)
            )
            client_count = await session.scalar(
                select(func.count())
                .select_from(Client)
                .where(
                    Client.platform_id == platform_id,
                    Client.external_id == external_id,
                )
            )
            active_count = await session.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(
                    Conversation.client_id == client_id,
                    Conversation.status == "active",
                )
            )

        assert platform_count == 1
        assert client_count == 1
        assert active_count == 1

        with pytest.raises(IntegrityError):
            async with session_factory() as session, session.begin():
                session.add(Conversation(client_id=client_id, status="active"))
                await session.flush()
    finally:
        try:
            if schema_ready:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(Platform).where(Platform.name == platform_name))
        finally:
            await engine.dispose()
