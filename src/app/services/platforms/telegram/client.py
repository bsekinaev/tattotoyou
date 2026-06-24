"""Асинхронный клиент для служебных сообщений Telegram Bot API."""

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.platforms.exceptions import PlatformHTTPError, PlatformTransportError

logger = get_logger(__name__)
settings = get_settings()


class TelegramClient:
    """Клиент Telegram для административных уведомлений."""

    def __init__(self) -> None:
        self.base_url = (
            f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}"
        )
        self._client = httpx.AsyncClient(
            timeout=10.0,
            trust_env=settings.http_trust_env,
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Отправить текстовое сообщение с явно выбранным режимом разметки."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode

        try:
            response = await self._client.post(
                f"{self.base_url}/sendMessage",
                json=payload,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.error(
                "telegram_send_http_error",
                status_code=status_code,
                chat_id=chat_id,
            )
            raise PlatformHTTPError(
                "Telegram API returned an error response",
                platform="telegram",
                status_code=status_code,
            ) from None
        except httpx.RequestError as exc:
            logger.error(
                "telegram_send_transport_error",
                error_type=type(exc).__name__,
                chat_id=chat_id,
            )
            raise PlatformTransportError(
                "Telegram API transport request failed",
                platform="telegram",
            ) from None

    async def close(self) -> None:
        """Корректно закрыть HTTP-клиент."""
        await self._client.aclose()