"""Репозиторий базы знаний и pgvector retrieval."""

from __future__ import annotations

import re

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.knowledge.models import KnowledgeBase
from app.infrastructure.db.repository import BaseRepository

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]{3,}")
_STOP_WORDS = {
    "как",
    "что",
    "это",
    "для",
    "или",
    "мне",
    "можно",
    "хочу",
    "есть",
    "про",
    "после",
}


class KnowledgeBaseRepository(BaseRepository[KnowledgeBase]):
    def __init__(self, session: AsyncSession):
        super().__init__(KnowledgeBase, session)

    async def get_active_by_category(
        self,
        category: str | None = None,
        limit: int = 50,
    ) -> list[KnowledgeBase]:
        query = (
            select(self.model)
            .where(self.model.is_active.is_(True))
            .order_by(self.model.priority.desc(), self.model.id.desc())
            .limit(limit)
        )
        if category:
            query = query.where(self.model.category == category)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def semantic_search(
        self,
        query_vector: list[float],
        *,
        top_k: int,
        threshold: float,
    ) -> list[dict[str, object]]:
        """Вернуть только записи, прошедшие cosine-similarity threshold."""
        distance = self.model.question_vector.cosine_distance(query_vector)
        similarity = (1 - distance).label("similarity")
        query = (
            select(self.model, similarity)
            .where(
                self.model.is_active.is_(True),
                self.model.question_vector.is_not(None),
                distance <= 1 - threshold,
            )
            .order_by(distance.asc(), self.model.priority.desc(), self.model.id.asc())
            .limit(top_k)
        )
        rows = (await self.session.execute(query)).all()
        return [self._serialize(item, float(score), "semantic") for item, score in rows]

    async def keyword_search(self, text: str, *, limit: int) -> list[dict[str, object]]:
        """Консервативный fallback, если embedding runtime временно недоступен."""
        tokens = [
            token.casefold()
            for token in _TOKEN_RE.findall(text)
            if token.casefold() not in _STOP_WORDS
        ]
        tokens = list(dict.fromkeys(tokens))[:8]
        if not tokens:
            return []

        clauses = []
        for token in tokens:
            pattern = f"%{token}%"
            clauses.extend(
                [
                    self.model.question.ilike(pattern),
                    self.model.keywords.any(token),
                ]
            )

        query = (
            select(self.model)
            .where(self.model.is_active.is_(True), or_(*clauses))
            .order_by(self.model.priority.desc(), self.model.id.asc())
            .limit(limit)
        )
        items = list((await self.session.execute(query)).scalars().all())
        return [self._serialize(item, None, "keyword") for item in items]

    async def get_for_embedding_backfill(
        self,
        *,
        limit: int,
        force: bool = False,
        after_id: int = 0,
    ) -> list[KnowledgeBase]:
        query = (
            select(self.model)
            .where(self.model.id > after_id)
            .order_by(self.model.id.asc())
            .limit(limit)
        )
        if not force:
            query = query.where(self.model.question_vector.is_(None))
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def set_embedding(self, item: KnowledgeBase, embedding: list[float]) -> None:
        item.question_vector = embedding
        await self.session.flush()

    async def clear_embedding(self, item: KnowledgeBase) -> None:
        item.question_vector = None
        await self.session.flush()

    async def deactivate(self, id: int) -> bool:
        stmt = update(self.model).where(self.model.id == id).values(is_active=False)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    @staticmethod
    def _serialize(
        item: KnowledgeBase,
        similarity: float | None,
        source: str,
    ) -> dict[str, object]:
        return {
            "id": item.id,
            "category": item.category,
            "question": item.question,
            "answer": item.answer,
            "priority": item.priority,
            "similarity": similarity,
            "source": source,
        }
