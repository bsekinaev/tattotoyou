"""Надёжный асинхронный клиент GigaChat с distributed token cache."""

from __future__ import annotations

import asyncio
import base64
import random
import time
import uuid
from typing import Any

import httpx
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.tls import create_verified_ssl_context
from app.services.ai.exceptions import (
    GigaChatAuthenticationError,
    GigaChatError,
    GigaChatInvalidResponseError,
    GigaChatRateLimitError,
    GigaChatServerError,
    GigaChatTimeoutError,
    GigaChatTransportError,
)

logger = get_logger(__name__)
settings = get_settings()


class GigaChatClient:
    """GigaChat client with bounded retries and single-flight token refresh."""

    _TOKEN_KEY = "gigachat:access_token"
    _TOKEN_LOCK_KEY = "gigachat:access_token:refresh_lock"
    _TOKEN_TTL = 1800
    _RELEASE_LOCK_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """

    def __init__(self, redis_client: Redis | None = None):
        self.auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        self.completion_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        ssl_context = create_verified_ssl_context(settings.gigachat_ca_bundle)
        self._client = httpx.AsyncClient(
            verify=ssl_context,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
            trust_env=settings.http_trust_env,
        )
        self._redis = redis_client
        self._access_token: str | None = None

    async def _get_token(self, *, force_refresh: bool = False) -> str:
        if force_refresh:
            await self.invalidate_token()
        else:
            cached = await self._read_cached_token()
            if cached:
                return cached

        if self._redis is None:
            return await self._request_and_cache_token()

        owner = uuid.uuid4().hex
        try:
            acquired = bool(
                await self._redis.set(
                    self._TOKEN_LOCK_KEY,
                    owner,
                    nx=True,
                    ex=settings.gigachat_token_lock_ttl_seconds,
                )
            )
        except Exception as exc:
            logger.warning("gigachat_token_lock_unavailable", error_type=type(exc).__name__)
            return await self._request_and_cache_token()

        if acquired:
            try:
                cached = await self._read_redis_token()
                if cached:
                    self._access_token = cached
                    return cached
                return await self._request_and_cache_token()
            finally:
                await self._release_refresh_lock(owner)

        deadline = time.monotonic() + settings.gigachat_token_lock_wait_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(settings.gigachat_token_lock_poll_seconds)
            cached = await self._read_redis_token()
            if cached:
                self._access_token = cached
                logger.debug("gigachat_token_from_peer_refresh")
                return cached

        raise GigaChatTransportError(
            "GigaChat token refresh lock timed out",
            code="token_refresh_lock_timeout",
            retryable=True,
        )

    async def _read_cached_token(self) -> str | None:
        cached = await self._read_redis_token()
        if cached:
            self._access_token = cached
            logger.debug("gigachat_token_from_cache")
            return cached
        if self._access_token:
            logger.debug("gigachat_token_from_memory")
            return self._access_token
        return None

    async def _read_redis_token(self) -> str | None:
        if self._redis is None:
            return None
        try:
            value = await self._redis.get(self._TOKEN_KEY)
        except Exception as exc:
            logger.warning("redis_cache_read_failed", error_type=type(exc).__name__)
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    async def _request_and_cache_token(self) -> str:
        token = await self._request_token()
        self._access_token = token
        if self._redis is not None:
            try:
                await self._redis.set(self._TOKEN_KEY, token, ex=self._TOKEN_TTL)
                logger.info("gigachat_token_cached", ttl_seconds=self._TOKEN_TTL)
            except Exception as exc:
                logger.warning("redis_cache_write_failed", error_type=type(exc).__name__)
        return token

    async def _request_token(self) -> str:
        credentials = (
            f"{settings.gigachat_client_id.get_secret_value()}:"
            f"{settings.gigachat_client_secret.get_secret_value()}"
        )
        encoded = base64.b64encode(credentials.encode("ascii")).decode("ascii")
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {encoded}",
        }
        try:
            response = await self._client.post(
                self.auth_url,
                headers=headers,
                data={"scope": settings.gigachat_scope},
            )
        except httpx.TimeoutException:
            raise GigaChatTimeoutError(
                "GigaChat authentication timed out",
                code="auth_timeout",
                retryable=True,
            ) from None
        except httpx.RequestError:
            raise GigaChatTransportError(
                "GigaChat authentication transport failed",
                code="auth_transport",
                retryable=True,
            ) from None

        self._raise_for_error_status(response, operation="auth")
        try:
            payload = response.json()
            token = payload["access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise GigaChatInvalidResponseError(
                "GigaChat authentication response is invalid",
                code="invalid_auth_response",
                retryable=False,
            ) from None

        logger.info("gigachat_token_acquired")
        return token

    async def invalidate_token(self, stale_token: str | None = None) -> None:
        """Сбросить только тот cached token, который был отклонён сервером."""
        self._access_token = None
        if self._redis is None:
            return
        try:
            cached = await self._redis.get(self._TOKEN_KEY)
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")
            if stale_token is None or cached == stale_token:
                await self._redis.delete(self._TOKEN_KEY)
        except Exception as exc:
            logger.warning("gigachat_token_invalidation_failed", error_type=type(exc).__name__)

    async def generate_response(self, history: list[dict[str, str]]) -> str:
        """Получить ответ, повторяя только временные ошибки в заданных пределах."""
        attempt = 1
        token_refreshed = False
        while True:
            token: str | None = None
            try:
                token = await self._get_token()
                return await self._request_completion(history, token)
            except GigaChatAuthenticationError:
                if token is not None and not token_refreshed:
                    token_refreshed = True
                    await self.invalidate_token(token)
                    logger.warning("gigachat_token_rejected_refreshing")
                    continue
                raise
            except GigaChatError as exc:
                if not exc.retryable or attempt >= settings.gigachat_max_attempts:
                    logger.error(
                        "gigachat_request_exhausted",
                        error_code=exc.code,
                        attempts=attempt,
                    )
                    raise
                delay = self._retry_delay_seconds(attempt)
                logger.warning(
                    "gigachat_request_retrying",
                    error_code=exc.code,
                    attempt=attempt,
                    delay_seconds=delay,
                )
                await asyncio.sleep(delay)
                attempt += 1

    async def _request_completion(self, history: list[dict[str, str]], token: str) -> str:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload: dict[str, Any] = {
            "model": settings.gigachat_model,
            "messages": history,
            "temperature": 0.7,
            "max_tokens": settings.gigachat_max_output_tokens,
        }
        try:
            response = await self._client.post(
                self.completion_url,
                headers=headers,
                json=payload,
            )
        except httpx.TimeoutException:
            raise GigaChatTimeoutError(
                "GigaChat completion timed out",
                code="completion_timeout",
                retryable=True,
            ) from None
        except httpx.RequestError:
            raise GigaChatTransportError(
                "GigaChat completion transport failed",
                code="completion_transport",
                retryable=True,
            ) from None

        self._raise_for_error_status(response, operation="completion")
        try:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError
        except (IndexError, KeyError, TypeError, ValueError):
            raise GigaChatInvalidResponseError(
                "GigaChat completion response is invalid",
                code="invalid_completion_response",
                retryable=False,
            ) from None
        return content.strip()

    @staticmethod
    def _raise_for_error_status(response: httpx.Response, *, operation: str) -> None:
        status = response.status_code
        if status < 400:
            return
        if status in {401, 403}:
            raise GigaChatAuthenticationError(
                "GigaChat authentication was rejected",
                code=f"{operation}_authentication",
                retryable=False,
                status_code=status,
            )
        if status == 429:
            raise GigaChatRateLimitError(
                "GigaChat rate limit exceeded",
                code=f"{operation}_rate_limit",
                retryable=True,
                status_code=status,
            )
        if status >= 500:
            raise GigaChatServerError(
                "GigaChat server error",
                code=f"{operation}_server_error",
                retryable=True,
                status_code=status,
            )
        raise GigaChatError(
            "GigaChat request was rejected",
            code=f"{operation}_http_{status}",
            retryable=False,
            status_code=status,
        )

    @staticmethod
    def _retry_delay_seconds(attempt: int) -> float:
        base = settings.gigachat_retry_base_seconds * (2 ** max(0, attempt - 1))
        bounded = min(base, settings.gigachat_retry_max_seconds)
        return bounded + random.uniform(0.0, settings.gigachat_retry_jitter_seconds)

    async def _release_refresh_lock(self, owner: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.eval(
                self._RELEASE_LOCK_SCRIPT,
                1,
                self._TOKEN_LOCK_KEY,
                owner,
            )
        except Exception as exc:
            logger.warning("gigachat_token_lock_release_failed", error_type=type(exc).__name__)

    async def close(self) -> None:
        await self._client.aclose()
