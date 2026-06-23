"""Репозиторий для работы с базой знаний."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.knowledge.models import KnowledgeBase
from app.infrastructure.db.repository import BaseRepository


class KnowledgeBaseRepository(BaseRepository[KnowledgeBase]):
    def __init__(self, session: AsyncSession):
        super().__init__(KnowledgeBase, session)

    async def get_active_by_category(
        self,
        category: str | None = None,
        limit: int = 50,
    ) -> list[KnowledgeBase]:
        """
        Получить активные FAQ-записи с фильтрацией по категории.
        Сортировка по приоритету (desc), затем по ID (desc).
        """
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

    async def search_by_keyword(self, keyword: str) -> list[KnowledgeBase]:
        """
        Простой keyword-based поиск (без векторов).
        Используется как fallback, если pgvector недоступен.
        """
        # PostgreSQL array contains operator: &&
        # Ищем записи, где keywords содержит искомое слово
        query = (
            select(self.model)
            .where(
                self.model.is_active.is_(True),
                self.model.keywords.any(keyword),  # ANY operator for arrays
            )
            .order_by(self.model.priority.desc())
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def deactivate(self, id: int) -> bool:
        """
        Мягкое удаление: помечаем FAQ как неактивный.
        Данные сохраняются для аудита и возможного восстановления.
        """
        stmt = update(self.model).where(self.model.id == id).values(is_active=False)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0
