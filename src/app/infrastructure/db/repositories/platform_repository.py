"""Репозиторий для работы с платформами."""
from sqlalchemy import select
from app.domain.clients.models import Platform
from app.infrastructure.db.repository import BaseRepository


class PlatformRepository(BaseRepository[Platform]):
    def __init__(self, session):
        super().__init__(Platform, session)

    async def get_by_name(self, name: str) -> Platform | None:
        """Получить платформу по имени (telegram, vk, instagram)."""
        result = await self.session.execute(
            select(self.model).where(self.model.name == name)
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, name: str, **kwargs) -> Platform:
        """
        Get-or-Create паттерн.
        Возвращает существующую платформу или создаёт новую.
        """
        platform = await self.get_by_name(name)
        if not platform:
            platform = await self.create(name=name, **kwargs)
        return platform