"""Проверки разбиения длинных Telegram-сообщений."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.platforms.telegram_adapter import TelegramAdapter, split_telegram_text


def test_split_telegram_text_preserves_content_and_limits_chunks() -> None:
    text = ("слово " * 1000).strip()

    chunks = split_telegram_text(text, 400)

    assert all(0 < len(chunk) <= 400 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


class SequentialHTTPClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        self.calls.append({"url": url, "json": json})
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"ok": True, "result": {"message_id": len(self.calls)}},
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_adapter_sends_all_chunks_and_returns_last_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.platforms import telegram_adapter as module

    monkeypatch.setattr(module.settings, "telegram_message_chunk_size", 10)
    client = SequentialHTTPClient()
    adapter = TelegramAdapter()
    await adapter._client.aclose()
    adapter._client = client  # type: ignore[assignment]

    message_id = await adapter.send_message("123", "один два три четыре пять")

    assert len(client.calls) == 3
    assert all(len(call["json"]["text"]) <= 10 for call in client.calls)
    assert message_id == "3"
