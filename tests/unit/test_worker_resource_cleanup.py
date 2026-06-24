"""Regression tests for async resource cleanup in Telegram workers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.workers.tasks import process_telegram_update as task_module


class FakeTelegramAdapter:
    """Minimal adapter with observable parse and close operations."""

    def __init__(
        self, parsed_message: object | None = None, error: Exception | None = None
    ) -> None:
        self.parsed_message = parsed_message
        self.error = error
        self.close = AsyncMock()
        self.send_message = AsyncMock()

    async def parse_message(self, update_dict: dict) -> object | None:
        if self.error is not None:
            raise self.error
        return self.parsed_message


@pytest.mark.asyncio
async def test_process_closes_adapter_for_ignored_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeTelegramAdapter(parsed_message=None)
    monkeypatch.setattr(task_module, "TelegramAdapter", lambda: adapter)

    await task_module._process_async({"update_id": 1, "callback_query": {}})

    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_closes_adapter_when_parsing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FakeTelegramAdapter(error=ValueError("invalid update"))
    monkeypatch.setattr(task_module, "TelegramAdapter", lambda: adapter)

    with pytest.raises(ValueError, match="invalid update"):
        await task_module._process_async({"update_id": 2})

    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_closes_adapter_for_non_text_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = SimpleNamespace(text=None, chat_id="123")
    adapter = FakeTelegramAdapter(parsed_message=message)
    monkeypatch.setattr(task_module, "TelegramAdapter", lambda: adapter)

    await task_module._send_fallback({"update_id": 3, "message": {"photo": []}})

    adapter.send_message.assert_not_awaited()
    adapter.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_fallback_closes_adapter_when_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = SimpleNamespace(text="Привет", chat_id="123")
    adapter = FakeTelegramAdapter(parsed_message=message)
    adapter.send_message.side_effect = RuntimeError("telegram unavailable")
    monkeypatch.setattr(task_module, "TelegramAdapter", lambda: adapter)

    await task_module._send_fallback({"update_id": 4})

    adapter.close.assert_awaited_once()