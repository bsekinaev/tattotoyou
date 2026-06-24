"""Проверки предсказуемой конфигурации исходящих HTTP-клиентов."""

import importlib
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.platforms.telegram_adapter import TelegramAdapter
from app.services.platforms.telegram.client import TelegramClient


@pytest.mark.asyncio
async def test_telegram_adapter_does_not_inherit_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.core.platforms.telegram_adapter")
    http_client = Mock()
    http_client.aclose = AsyncMock()
    factory = Mock(return_value=http_client)
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)

    adapter = TelegramAdapter()

    assert factory.call_args.kwargs["trust_env"] is False
    await adapter.close()


@pytest.mark.asyncio
async def test_telegram_client_does_not_inherit_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.services.platforms.telegram.client")
    http_client = Mock()
    http_client.aclose = AsyncMock()
    factory = Mock(return_value=http_client)
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)

    client = TelegramClient()

    assert factory.call_args.kwargs["trust_env"] is False
    await client.close()


@pytest.mark.asyncio
async def test_gigachat_client_does_not_inherit_environment_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.services.ai.gigachat_client")
    ssl_context = Mock()
    http_client = Mock()
    http_client.aclose = AsyncMock()
    factory = Mock(return_value=http_client)
    monkeypatch.setattr(module, "create_verified_ssl_context", Mock(return_value=ssl_context))
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)

    client = module.GigaChatClient()

    assert factory.call_args.kwargs["trust_env"] is False
    await client.close()


def test_importing_gigachat_module_does_not_create_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("app.services.ai.gigachat_client")
    factory = Mock()
    monkeypatch.setattr(module.httpx, "AsyncClient", factory)

    importlib.reload(module)

    factory.assert_not_called()
