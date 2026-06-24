"""Регрессионные тесты локальной инфраструктуры и startup-режимов."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from app.core.config import Settings


def _settings_kwargs() -> dict[str, object]:
    return {
        "telegram_bot_token": SecretStr("telegram-token"),
        "telegram_webhook_secret": SecretStr("telegram-secret"),
        "telegram_admin_chat_id": 123,
        "gigachat_client_id": SecretStr("gigachat-client"),
        "gigachat_client_secret": SecretStr("gigachat-secret"),
        "postgres_password": SecretStr("postgres-password"),
        "secret_key": SecretStr("secret-key-at-least-32-characters"),
        "admin_api_key": SecretStr("admin-key-at-least-32-characters-long"),
    }


def test_local_infrastructure_defaults_match_development_compose() -> None:
    settings = Settings(_env_file=None, **_settings_kwargs())

    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5433
    assert settings.redis_host == "localhost"
    assert settings.redis_port == 6380
    assert settings.startup_require_dependencies is False
    assert settings.postgres_connect_timeout_seconds == 5
    assert settings.redis_connect_timeout_seconds == 5


@pytest.mark.asyncio
async def test_local_startup_enters_degraded_mode_when_dependencies_are_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as main_module

    settings = SimpleNamespace(
        app_name="Tattoo Assistant",
        app_version="0.1.0",
        debug=True,
        startup_require_dependencies=False,
        postgres_host="localhost",
        postgres_port=5433,
        redis_host="localhost",
        redis_port=6380,
        redis_url="redis://localhost:6380/0",
        redis_connect_timeout_seconds=1,
    )
    redis_client = SimpleNamespace(
        ping=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        aclose=AsyncMock(),
    )
    close_db = AsyncMock()

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "init_db",
        AsyncMock(side_effect=ConnectionError("database unavailable")),
    )
    monkeypatch.setattr(main_module, "close_db", close_db)
    monkeypatch.setattr(main_module.aioredis, "from_url", Mock(return_value=redis_client))

    app = SimpleNamespace(state=SimpleNamespace())

    async with main_module.lifespan(app):
        assert app.state.redis is redis_client
        assert app.state.startup_unavailable_dependencies == ("database", "redis")

    redis_client.aclose.assert_awaited_once()
    close_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_strict_startup_fails_when_required_dependencies_are_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main as main_module

    settings = SimpleNamespace(
        app_name="Tattoo Assistant",
        app_version="0.1.0",
        debug=False,
        startup_require_dependencies=True,
        postgres_host="postgres",
        postgres_port=5432,
        redis_host="redis",
        redis_port=6379,
        redis_url="redis://redis:6379/0",
        redis_connect_timeout_seconds=1,
    )
    redis_client = SimpleNamespace(
        ping=AsyncMock(side_effect=ConnectionError("redis unavailable")),
        aclose=AsyncMock(),
    )
    close_db = AsyncMock()

    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr(
        main_module,
        "init_db",
        AsyncMock(side_effect=ConnectionError("database unavailable")),
    )
    monkeypatch.setattr(main_module, "close_db", close_db)
    monkeypatch.setattr(main_module.aioredis, "from_url", Mock(return_value=redis_client))

    app = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(RuntimeError, match="database, redis"):
        async with main_module.lifespan(app):
            pytest.fail("strict startup must not yield")

    redis_client.aclose.assert_awaited_once()
    close_db.assert_awaited_once()
