"""Проверить локальные подключения к PostgreSQL и Redis без раскрытия секретов."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.infrastructure.db.session import async_engine


async def _check_database() -> tuple[bool, str]:
    try:
        async with async_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        return False, type(exc).__name__
    return True, "ok"


async def _check_redis(settings: Settings) -> tuple[bool, str]:
    client = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_connect_timeout_seconds,
        retry_on_timeout=False,
    )
    try:
        await client.ping()
    except Exception as exc:
        return False, type(exc).__name__
    finally:
        await client.aclose()
    return True, "ok"


def _status_line(name: str, target: str, result: tuple[bool, str]) -> str:
    ok, detail = result
    status = "OK" if ok else f"FAILED ({detail})"
    return f"{name}: {status} — {target}"


async def main() -> int:
    settings = get_settings()
    postgres_target = (
        f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
    redis_target = f"{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"

    database_result, redis_result = await asyncio.gather(
        _check_database(),
        _check_redis(settings),
    )

    print(_status_line("PostgreSQL", postgres_target, database_result))
    print(_status_line("Redis", redis_target, redis_result))

    if database_result[0] and redis_result[0]:
        print("Infrastructure preflight: OK")
        return 0

    print("\nЗапустите локальную инфраструктуру:")
    print(
        "docker compose -f docker-compose.yml "
        "-f docker-compose.dev.yml up -d postgres redis"
    )
    print("Затем проверьте, что .env использует PostgreSQL 5433 и Redis 6380.")
    return 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
