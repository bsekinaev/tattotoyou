"""
Проверка подключения к PostgreSQL и создание тестовых данных.
"""

import asyncio
import sys

from app.domain.clients.models import Client, Platform

# ============================================
# 🛠️ ФИКС ДЛЯ WINDOWS
# ============================================
# asyncpg плохо работает с дефолтным для Windows ProactorEventLoop.
# Принудительно переключаемся на SelectorEventLoop.
# В Linux/Docker это не нужно, там и так используется epoll (аналог Selector).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# ============================================

from sqlalchemy import select

#  Добавляем импорт и вызов настройки логирования
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import async_engine, async_session_factory

setup_logging()


async def test_db_connection():
    """Проверяем коннект и создаём тестовые записи."""
    settings = get_settings()
    print(f"🔌 Подключаемся к: {settings.postgres_dsn.split('@')[1]}")

    # 1. Создаём ВСЕ таблицы (для теста, в проде будем использовать Alembic)
    print("\n📦 Создаём таблицы в БД...")
    async with async_engine.begin() as conn:
        # drop_all + create_all для чистого теста
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Таблицы созданы")

    # 2. Создаём тестовую платформу
    print("\n📱 Создаём тестовую платформу Telegram...")
    async with async_session_factory() as session:
        telegram = Platform(name="telegram", is_active=True)
        session.add(telegram)
        await session.commit()
        await session.refresh(telegram)  # Обновляем объект, чтобы получить id
        print(f"✅ Platform создана: id={telegram.id}, name={telegram.name}")

        # 3. Создаём тестового клиента
        print("\n👤 Создаём тестового клиента...")
        client = Client(
            platform_id=telegram.id,
            external_id="123456789",
            display_name="Тестовый Клиент",
            username="test_user",
            is_vip=False,
        )
        session.add(client)
        await session.commit()
        await session.refresh(client)
        print(f"✅ Client создан: id={client.id}, name={client.display_name}")

        # 4. Проверяем SELECT
        print("\n🔎 Делаем SELECT запрос...")
        result = await session.execute(select(Client).where(Client.platform_id == telegram.id))
        clients = result.scalars().all()
        print(f"✅ Найдено клиентов: {len(clients)}")
        for c in clients:
            print(f"   - {c.display_name} (@{c.username})")

    print("\n🎉 Все тесты пройдены! БД работает корректно.")


if __name__ == "__main__":
    asyncio.run(test_db_connection())