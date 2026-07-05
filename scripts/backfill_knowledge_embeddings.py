"""Backfill embeddings для существующих FAQ.

Запуск:
    python scripts/backfill_knowledge_embeddings.py
    python scripts/backfill_knowledge_embeddings.py --force
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infrastructure.db.repositories import KnowledgeBaseRepository
from app.infrastructure.db.session import async_session_factory
from app.services.ai.embedding_service import (
    build_knowledge_embedding_text,
    generate_embeddings,
)

logger = get_logger(__name__)
settings = get_settings()


async def backfill(*, force: bool = False) -> int:
    processed = 0
    last_id = 0
    async with async_session_factory() as session:
        repo = KnowledgeBaseRepository(session)
        while True:
            items = await repo.get_for_embedding_backfill(
                limit=settings.embedding_backfill_batch_size,
                force=force,
                after_id=last_id,
            )
            if not items:
                break

            embeddings = await generate_embeddings(
                [
                    build_knowledge_embedding_text(
                        question=item.question,
                        answer=item.answer,
                        keywords=item.keywords,
                    )
                    for item in items
                ]
            )
            for item, embedding in zip(items, embeddings, strict=True):
                await repo.set_embedding(item, embedding)
            await session.commit()
            processed += len(items)
            last_id = items[-1].id
            logger.info("knowledge_embeddings_backfilled", batch=len(items), total=processed)

    return processed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Пересчитать все embeddings")
    return parser.parse_args()


if __name__ == "__main__":
    setup_logging()
    count = asyncio.run(backfill(force=parse_args().force))
    print(f"Knowledge embeddings updated: {count}")
