"""
Telegram-specific адаптер.
Реализует PlatformAdapter для Telegram Bot API.
"""

from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.platforms.base import PlatformAdapter, PlatformMessage, PlatformUser
from app.core.platforms.exceptions import PlatformHTTPError, PlatformTransportError

logger = get_logger(__name__)
settings = get_settings()


def split_telegram_text(text: str, max_length: int) -> list[str]:
    """Разбить длинный plain-text по границам абзацев/слов."""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        window = remaining[:max_length]
        split_at = max(window.rfind("\n"), window.rfind(" "))
        if split_at < max_length // 2:
            split_at = max_length

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:max_length]
            split_at = max_length
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()

    return chunks


class TelegramAdapter(PlatformAdapter):
    """Telegram Bot API adapter."""

    def __init__(self) -> None:
        self.bot_token = settings.telegram_bot_token.get_secret_value()
        self.webhook_secret = settings.telegram_webhook_secret.get_secret_value()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._client = httpx.AsyncClient(
            timeout=10.0,
            trust_env=settings.http_trust_env,
        )

    @property
    def platform_name(self) -> str:
        return "telegram"

    async def send_message(self, chat_id: str, text: str) -> str:
        """Отправить plain-text, безопасно разбивая его под лимит Telegram."""
        last_message_id: str | None = None
        try:
            for chunk in split_telegram_text(text, settings.telegram_message_chunk_size):
                response = await self._client.post(
                    f"{self.base_url}/sendMessage",
                    json={
                        "chat_id": int(chat_id),
                        "text": chunk,
                    },
                )
                response.raise_for_status()
                result = response.json()
                last_message_id = str(result["result"]["message_id"])
            if last_message_id is None:
                raise ValueError("Telegram message is empty")
            return last_message_id
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.error(
                "telegram_send_http_error",
                status_code=status_code,
                chat_id=chat_id,
            )
            raise PlatformHTTPError(
                "Telegram API returned an error response",
                platform=self.platform_name,
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
                platform=self.platform_name,
            ) from None

    async def verify_webhook(self, request_data: dict, headers: dict) -> bool:
        """Проверить Telegram webhook secret token."""
        secret_token = headers.get("x-telegram-bot-api-secret-token")
        return secret_token == self.webhook_secret

    async def parse_message(self, request_data: dict) -> PlatformMessage | None:
        """Распарсить Telegram Update в PlatformMessage."""
        if "message" not in request_data:
            return None

        message = request_data["message"]

        if "text" not in message:
            return None

        from_user = message.get("from", {})
        user = PlatformUser(
            external_id=str(from_user.get("id", "")),
            display_name=from_user.get("first_name"),
            username=from_user.get("username"),
            is_bot=from_user.get("is_bot", False),
        )

        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))

        timestamp = None
        if "date" in message:
            timestamp = datetime.fromtimestamp(message["date"], tz=UTC)

        return PlatformMessage(
            platform="telegram",
            message_id=str(message.get("message_id", "")),
            chat_id=chat_id,
            user=user,
            text=message.get("text"),
            timestamp=timestamp,
        )

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.aclose()
