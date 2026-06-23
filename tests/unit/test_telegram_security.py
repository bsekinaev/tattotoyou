"""Regression tests for Telegram HTML and secret-safe error handling."""

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.core.platforms.exceptions import PlatformHTTPError
from app.core.platforms.telegram_adapter import TelegramAdapter
from app.services.notifications import admin_notifier
from app.services.notifications.admin_notifier import AdminNotifier
from app.services.platforms.telegram.client import TelegramClient


class RecordingHTTPClient:
    """Minimal async HTTP client used to inspect outgoing Telegram payloads."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        self.calls.append({"url": url, "json": json})
        return self.response

    async def aclose(self) -> None:
        return None


class RaisingHTTPClient:
    """Minimal async client that raises a configured transport error."""

    def __init__(self, error: httpx.RequestError) -> None:
        self.error = error

    async def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        raise self.error

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_user_facing_adapter_sends_plain_text_without_html_mode() -> None:
    request = httpx.Request("POST", "https://api.telegram.org/sendMessage")
    response = httpx.Response(
        200,
        request=request,
        json={"ok": True, "result": {"message_id": 17}},
    )
    fake_client = RecordingHTTPClient(response)

    adapter = TelegramAdapter()
    await adapter._client.aclose()
    adapter._client = fake_client  # type: ignore[assignment]

    result = await adapter.send_message("123", "<b>не разметка</b>")

    assert result == "17"
    assert fake_client.calls[0]["json"] == {
        "chat_id": 123,
        "text": "<b>не разметка</b>",
    }


@pytest.mark.asyncio
async def test_admin_notification_escapes_untrusted_html(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    class FakeTelegramClient:
        async def send_message(
            self,
            chat_id: int,
            text: str,
            *,
            parse_mode: str | None = None,
        ) -> dict[str, Any]:
            sent.update(chat_id=chat_id, text=text, parse_mode=parse_mode)
            return {"ok": True}

        async def close(self) -> None:
            return None

    monkeypatch.setattr(admin_notifier, "TelegramClient", FakeTelegramClient)

    await AdminNotifier.notify_escalation(
        client_name="<b>Клиент</b>",
        client_username="name<script>",
        reason="health&urgent",
        last_message="<a href='https://evil.example'>нажми</a>",
        chat_id=777,
    )

    assert sent["parse_mode"] == "HTML"
    assert "&lt;b&gt;Клиент&lt;/b&gt;" in sent["text"]
    assert "@name&lt;script&gt;" in sent["text"]
    assert "health&amp;urgent" in sent["text"]
    assert "&lt;a href=&#x27;https://evil.example&#x27;&gt;" in sent["text"]
    assert "<a href=" not in sent["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("client_factory", [TelegramAdapter, TelegramClient])
async def test_telegram_http_errors_do_not_expose_bot_token(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: type[TelegramAdapter] | type[TelegramClient],
) -> None:
    token = "test-token-that-must-not-be-logged"
    request = httpx.Request(
        "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
    )
    response = httpx.Response(500, request=request)
    fake_client = RecordingHTTPClient(response)
    logger = MagicMock()

    if client_factory is TelegramAdapter:
        from app.core.platforms import telegram_adapter as module

        monkeypatch.setattr(module, "logger", logger)
        client = TelegramAdapter()
        await client._client.aclose()
        client._client = fake_client  # type: ignore[assignment]
        send = client.send_message("123", "hello")
    else:
        from app.services.platforms.telegram import client as module

        monkeypatch.setattr(module, "logger", logger)
        client = TelegramClient()
        await client._client.aclose()
        client._client = fake_client  # type: ignore[assignment]
        send = client.send_message(123, "hello")

    with pytest.raises(PlatformHTTPError) as error:
        await send

    assert str(error.value) == "Telegram API returned an error response"
    assert error.value.status_code == 500
    assert token not in repr(logger.error.call_args)
    assert "api.telegram.org" not in repr(logger.error.call_args)


@pytest.mark.asyncio
@pytest.mark.parametrize("client_factory", [TelegramAdapter, TelegramClient])
async def test_telegram_transport_errors_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    client_factory: type[TelegramAdapter] | type[TelegramClient],
) -> None:
    token = "transport-token-that-must-not-be-logged"
    request = httpx.Request(
        "POST",
        f"https://api.telegram.org/bot{token}/sendMessage",
    )
    fake_client = RaisingHTTPClient(httpx.ConnectError("connection failed", request=request))
    logger = MagicMock()

    if client_factory is TelegramAdapter:
        from app.core.platforms import telegram_adapter as module

        monkeypatch.setattr(module, "logger", logger)
        client = TelegramAdapter()
        await client._client.aclose()
        client._client = fake_client  # type: ignore[assignment]
        send = client.send_message("123", "hello")
    else:
        from app.services.platforms.telegram import client as module

        monkeypatch.setattr(module, "logger", logger)
        client = TelegramClient()
        await client._client.aclose()
        client._client = fake_client  # type: ignore[assignment]
        send = client.send_message(123, "hello")

    from app.core.platforms.exceptions import PlatformTransportError

    with pytest.raises(PlatformTransportError) as error:
        await send

    assert str(error.value) == "Telegram API transport request failed"
    assert token not in repr(logger.error.call_args)
    assert "api.telegram.org" not in repr(logger.error.call_args)
