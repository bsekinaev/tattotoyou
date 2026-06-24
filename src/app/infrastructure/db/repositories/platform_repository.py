"""Репозиторий для работы с платформами."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.domain.clients.models import Platform
from app.infrastructure.db.repository import BaseRepository


class PlatformRepository(BaseRepository[Platform]):
    def __init__(self, session):
        super().__init__(Platform, session)

    async def get_by_name(self, name: str) -> Platform | None:
        """Получить платформу по имени (telegram, vk, instagram)."""
        result = await self.session.execute(select(self.model).where(self.model.name == name))
        return result.scalar_one_or_none()

    async def get_or_create(self, name: str, **kwargs: Any) -> Platform:
        """Атомарно получить существующую платформу или создать новую.

        ``INSERT .. ON CONFLICT DO NOTHING`` устраняет гонку между параллельными
        worker-процессами без полного rollback текущей транзакции.
        """
        values = {"name": name, **kwargs}
        values.setdefault("is_active", True)

        statement = (
            insert(self.model)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[self.model.name])
            .returning(self.model.id)
        )
        result = await self.session.execute(statement)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            platform = await self.get_by_id(inserted_id)
        else:
            platform = await self.get_by_name(name)

        if platform is None:
            raise RuntimeError("Platform upsert completed without a visible row")
        return platform
