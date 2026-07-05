"""Репозиторий для работы с клиентами."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

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
                self.model.platform_id == platform_id,
                self.model.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        platform_id: int,
        external_id: str,
        **kwargs: Any,
    ) -> Client:
        """Атомарно получить существующего клиента или создать нового.

        Конфликт по ``(platform_id, external_id)`` разрешается самой БД. Это
        сохраняет транзакцию пригодной для дальнейшей работы и не требует
        ``session.rollback()`` внутри репозитория.
        """
        values = {
            "platform_id": platform_id,
            "external_id": external_id,
            **kwargs,
        }
        values.setdefault("is_vip", False)
        values.setdefault("is_banned", False)

        statement = (
            insert(self.model)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[
                    self.model.platform_id,
                    self.model.external_id,
                ]
            )
            .returning(self.model.id)
        )
        result = await self.session.execute(statement)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            client = await self.get_by_id(inserted_id)
        else:
            client = await self.get_by_platform_and_external_id(
                platform_id,
                external_id,
            )

        if client is None:
            raise RuntimeError("Client upsert completed without a visible row")

        profile_updates: dict[str, Any] = {}
        display_name = kwargs.get("display_name")
        username = kwargs.get("username")
        if display_name and display_name != client.display_name:
            profile_updates["display_name"] = display_name
        if username != client.username:
            profile_updates["username"] = username
        if profile_updates:
            await self.update(client, **profile_updates)

        return client
