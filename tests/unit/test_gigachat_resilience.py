"""Проверки retry, refresh и безопасных ошибок GigaChat."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.ai.exceptions import (
    GigaChatAuthenticationError,
    GigaChatRateLimitError,
    GigaChatServerError,
)
from app.services.ai.gigachat_client import GigaChatClient


@pytest.mark.asyncio
async def test_rejected_access_token_is_invalidated_and_refreshed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GigaChatClient()
    await client._client.aclose()
    client._get_token = AsyncMock(side_effect=["stale-token", "fresh-token"])  # type: ignore[method-assign]
    client.invalidate_token = AsyncMock()  # type: ignore[method-assign]
    client._request_completion = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            GigaChatAuthenticationError(
                "rejected",
                code="completion_authentication",
                retryable=False,
                status_code=401,
            ),
            "Готовый ответ",
        ]
    )

    result = await client.generate_response([{"role": "user", "content": "Привет"}])

    assert result == "Готовый ответ"
    client.invalidate_token.assert_awaited_once_with("stale-token")
    assert client._request_completion.await_args_list[1].args[1] == "fresh-token"


@pytest.mark.asyncio
async def test_retryable_error_uses_backoff_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GigaChatClient()
    await client._client.aclose()
    client._get_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    client._request_completion = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            GigaChatRateLimitError(
                "rate limited",
                code="completion_rate_limit",
                retryable=True,
                status_code=429,
            ),
            "Ответ после повтора",
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.ai.gigachat_client.asyncio.sleep", sleep)
    monkeypatch.setattr(client, "_retry_delay_seconds", lambda _attempt: 1.25)

    result = await client.generate_response([{"role": "user", "content": "Цена?"}])

    assert result == "Ответ после повтора"
    sleep.assert_awaited_once_with(1.25)
    assert client._request_completion.await_count == 2


@pytest.mark.asyncio
async def test_retryable_error_is_raised_after_attempts_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GigaChatClient()
    await client._client.aclose()
    client._get_token = AsyncMock(return_value="token")  # type: ignore[method-assign]
    error = GigaChatServerError(
        "server unavailable",
        code="completion_server_error",
        retryable=True,
        status_code=503,
    )
    client._request_completion = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    monkeypatch.setattr("app.services.ai.gigachat_client.asyncio.sleep", AsyncMock())
    monkeypatch.setattr(client, "_retry_delay_seconds", lambda _attempt: 0.0)

    with pytest.raises(GigaChatServerError) as raised:
        await client.generate_response([{"role": "user", "content": "Привет"}])

    assert raised.value.code == "completion_server_error"
    assert client._request_completion.await_count >= 1


class PeerRefreshRedis:
    def __init__(self) -> None:
        self.get_calls = 0
        self.set = AsyncMock(return_value=False)
        self.eval = AsyncMock()

    async def get(self, _key: str) -> str | None:
        self.get_calls += 1
        if self.get_calls >= 2:
            return "peer-token"
        return None


@pytest.mark.asyncio
async def test_token_refresh_waits_for_peer_instead_of_requesting_duplicate_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = PeerRefreshRedis()
    client = GigaChatClient(redis_client=redis)  # type: ignore[arg-type]
    await client._client.aclose()
    request_token = AsyncMock(return_value="unexpected-token")
    client._request_token = request_token  # type: ignore[method-assign]
    monkeypatch.setattr("app.services.ai.gigachat_client.asyncio.sleep", AsyncMock())

    token = await client._get_token()

    assert token == "peer-token"
    request_token.assert_not_awaited()
    redis.set.assert_awaited_once()


@pytest.mark.parametrize(
    ("status_code", "exception_type", "retryable"),
    [
        (401, GigaChatAuthenticationError, False),
        (429, GigaChatRateLimitError, True),
        (503, GigaChatServerError, True),
        (400, Exception, False),
    ],
)
def test_http_statuses_are_classified_without_response_body_leakage(
    status_code: int,
    exception_type: type[Exception],
    retryable: bool,
) -> None:
    request = httpx.Request("POST", "https://gigachat.example/secret-path")
    response = httpx.Response(status_code, request=request, text="sensitive provider body")

    with pytest.raises(exception_type) as raised:
        GigaChatClient._raise_for_error_status(response, operation="completion")

    error = raised.value
    assert getattr(error, "retryable", retryable) is retryable
    assert "sensitive provider body" not in str(error)
    assert "secret-path" not in str(error)
