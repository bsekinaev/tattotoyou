"""Регрессионные тесты ограничений Telegram webhook."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

from app.core.middleware import RequestBodyLimitMiddleware
from app.services.platforms.telegram.schemas import (
    TELEGRAM_NAME_MAX_LENGTH,
    TELEGRAM_TEXT_MAX_LENGTH,
    TelegramUpdate,
)

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _scope(
    *,
    path: str = "/webhook/telegram",
    content_length: int | None = None,
) -> Scope:
    headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode("ascii")))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }


async def _run_asgi(
    app: ASGIApp,
    *,
    scope: Scope,
    messages: list[Message],
) -> list[Message]:
    pending = list(messages)
    sent: list[Message] = []

    async def receive() -> Message:
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _request_message(body: bytes, *, more_body: bool = False) -> Message:
    return {
        "type": "http.request",
        "body": body,
        "more_body": more_body,
    }


def _response_status(messages: list[Message]) -> int:
    start = next(message for message in messages if message["type"] == "http.response.start")
    return int(start["status"])


@pytest.mark.asyncio
async def test_declared_oversized_body_is_rejected_without_calling_endpoint() -> None:
    endpoint_called = False

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal endpoint_called
        endpoint_called = True
        await JSONResponse({"status": "unexpected"})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        endpoint,
        max_body_bytes=16,
        paths=("/webhook/telegram",),
    )
    sent = await _run_asgi(
        middleware,
        scope=_scope(content_length=17),
        messages=[_request_message(b"")],
    )

    assert _response_status(sent) == 413
    assert endpoint_called is False


@pytest.mark.asyncio
async def test_stream_limit_cannot_be_bypassed_with_forged_content_length() -> None:
    endpoint_called = False

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal endpoint_called
        endpoint_called = True
        await JSONResponse({"status": "unexpected"})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        endpoint,
        max_body_bytes=8,
        paths=("/webhook/telegram",),
    )
    sent = await _run_asgi(
        middleware,
        scope=_scope(content_length=1),
        messages=[
            _request_message(b"1234", more_body=True),
            _request_message(b"56789"),
        ],
    )

    assert _response_status(sent) == 413
    assert endpoint_called is False


@pytest.mark.asyncio
async def test_body_at_limit_is_replayed_unchanged_to_endpoint() -> None:
    received_body = b""

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal received_body
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            received_body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        await JSONResponse({"status": "ok"})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        endpoint,
        max_body_bytes=8,
        paths=("/webhook/telegram",),
    )
    sent = await _run_asgi(
        middleware,
        scope=_scope(),
        messages=[
            _request_message(b"1234", more_body=True),
            _request_message(b"5678"),
        ],
    )

    assert _response_status(sent) == 200
    assert received_body == b"12345678"


@pytest.mark.asyncio
async def test_non_webhook_path_is_not_limited() -> None:
    received_body = b""

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal received_body
        message = await receive()
        received_body = message.get("body", b"")
        await JSONResponse({"status": "ok"})(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(
        endpoint,
        max_body_bytes=4,
        paths=("/webhook/telegram",),
    )
    sent = await _run_asgi(
        middleware,
        scope=_scope(path="/admin/knowledge", content_length=10),
        messages=[_request_message(b"0123456789")],
    )

    assert _response_status(sent) == 200
    assert received_body == b"0123456789"


def _valid_update(**message_overrides: Any) -> dict[str, Any]:
    message = {
        "message_id": 1,
        "from": {
            "id": 10,
            "is_bot": False,
            "first_name": "Соня",
        },
        "chat": {
            "id": 10,
            "type": "private",
            "first_name": "Соня",
        },
        "date": 1,
        "text": "Привет",
    }
    message.update(message_overrides)
    return {"update_id": 1, "message": message}


def test_telegram_text_length_is_bounded() -> None:
    TelegramUpdate.model_validate(_valid_update(text="x" * TELEGRAM_TEXT_MAX_LENGTH))

    with pytest.raises(ValidationError):
        TelegramUpdate.model_validate(_valid_update(text="x" * (TELEGRAM_TEXT_MAX_LENGTH + 1)))


def test_telegram_name_length_is_bounded() -> None:
    TelegramUpdate.model_validate(
        _valid_update(
            **{
                "from": {
                    "id": 10,
                    "is_bot": False,
                    "first_name": "x" * TELEGRAM_NAME_MAX_LENGTH,
                }
            }
        )
    )

    with pytest.raises(ValidationError):
        TelegramUpdate.model_validate(
            _valid_update(
                **{
                    "from": {
                        "id": 10,
                        "is_bot": False,
                        "first_name": "x" * (TELEGRAM_NAME_MAX_LENGTH + 1),
                    }
                }
            )
        )


def test_application_rejects_oversized_webhook_before_endpoint() -> None:
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    limit = get_settings().telegram_webhook_max_body_bytes
    client = TestClient(create_app())
    response = client.post(
        "/webhook/telegram",
        content=b"x" * (limit + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}
    assert response.headers["x-request-id"]


def test_small_webhook_reaches_secret_validation() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.post(
        "/webhook/telegram",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "invalid"},
    )

    assert response.status_code == 403
