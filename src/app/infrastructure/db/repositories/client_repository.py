"""Репозиторий для работы с клиентами."""

from sqlalchemy import select

from app.domain.clients.models import Client
from app.infrastructure.db.repository import BaseRepository


class ClientRepository(BaseRepository[Client]):
    def __init__(self, session):
        super().__init__(Client, session)

    async def get_by_platform_and_external_id(
        self, platform_id: int, external_id: str
    ) -> Client | None:
        """Получить клиента по платформе и внешнему ID."""
        result = await self.session.execute(
            select(self.model).where(
                self.model.platform_id == platform_id, self.model.external_id == external_id
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, platform_id: int, external_id: str, **kwargs) -> Client:
        """
        Get-or-Create паттерн для клиентов.
        """
        client = await self.get_by_platform_and_external_id(platform_id, external_id)
        if not client:
            client = await self.create(platform_id=platform_id, external_id=external_id, **kwargs)
        return client