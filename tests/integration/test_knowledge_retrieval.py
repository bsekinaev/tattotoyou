"""PostgreSQL integration test pgvector retrieval."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.knowledge.models import KnowledgeBase
from app.infrastructure.db.repositories import KnowledgeBaseRepository

pytestmark = pytest.mark.integration


def _test_dsn() -> str:
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


@pytest.mark.asyncio
async def test_semantic_search_applies_similarity_threshold() -> None:
    engine = create_async_engine(_test_dsn(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    ids: list[int] = []
    first = [1.0] + [0.0] * 383
    second = [0.0, 1.0] + [0.0] * 382

    try:
        async with engine.connect() as connection:
            column = await connection.scalar(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'knowledge_base' AND column_name = 'question_vector'
                    """
                )
            )
            if column is None:
                pytest.fail("Run `python -m alembic upgrade head` before integration tests")

        async with session_factory() as session, session.begin():
            entries = [
                KnowledgeBase(
                    category="test",
                    question=f"relevant-{suffix}",
                    answer="relevant",
                    keywords=["relevant"],
                    question_vector=first,
                    priority=1,
                    is_active=True,
                ),
                KnowledgeBase(
                    category="test",
                    question=f"irrelevant-{suffix}",
                    answer="irrelevant",
                    keywords=["irrelevant"],
                    question_vector=second,
                    priority=100,
                    is_active=True,
                ),
            ]
            session.add_all(entries)
            await session.flush()
            ids = [entry.id for entry in entries]

        async with session_factory() as session:
            result = await KnowledgeBaseRepository(session).semantic_search(
                first, top_k=3, threshold=0.8
            )

        assert [item["id"] for item in result] == [ids[0]]
        assert result[0]["similarity"] == pytest.approx(1.0)
    finally:
        if ids:
            async with session_factory() as session, session.begin():
                await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(ids)))
        await engine.dispose()
