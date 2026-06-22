"""
Асинхронный клиент для работы с Telegram Bot API.
"""

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


class TelegramClient:
    def __init__(self):
        self.base_url = (
            f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}"
        )
        # Используем один AsyncClient на всё приложение для Keep-Alive
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send_message(self, chat_id: int, text: str) -> dict:
        """Отправка текстового сообщения."""
        try:
            response = await self._client.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error("telegram_send_failed", error=str(e), chat_id=chat_id)
            raise

    async def close(self):
        """Корректное закрытие сессии httpx."""
        await self._client.aclose()


# Создаём глобальный инстанс
tg_client = TelegramClient()


def get_telegram_client() -> TelegramClient:
    """Dependency для FastAPI, возвращает синглтон TelegramClient."""
    return tg_client
