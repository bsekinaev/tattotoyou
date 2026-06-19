"""
Клиент для работы с API Сбер GigaChat.
"""
import base64
import uuid
import warnings
import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

# У Сбера специфические сертификаты, для MVP отключаем строгую проверку SSL
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = get_logger(__name__)
settings = get_settings()


class GigaChatClient:
    def __init__(self):
        self.auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        self.completion_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        self._client = httpx.AsyncClient(verify=False, timeout=30.0)
        self._access_token: str | None = None

    async def _get_token(self) -> str:
        """Получение OAuth токена (с кэшированием в памяти для MVP)."""
        if self._access_token:
            return self._access_token

        # Формируем Basic Auth
        credentials = f"{settings.gigachat_client_id.get_secret_value()}:{settings.gigachat_client_secret.get_secret_value()}"
        b64_credentials = base64.b64encode(credentials.encode("ascii")).decode("ascii")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {b64_credentials}",
        }
        data = {"scope": settings.gigachat_scope}

        try:
            response = await self._client.post(self.auth_url, headers=headers, data=data)
            response.raise_for_status()
            self._access_token = response.json()["access_token"]
            logger.info("gigachat_token_acquired")
            return self._access_token
        except httpx.HTTPError as e:
            logger.error("gigachat_auth_failed", error=str(e))
            raise

    async def generate_response(self, history: list[dict]) -> str:
        """
        Генерация ответа на основе истории диалога.
        history: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        """
        token = await self._get_token()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {
            "model": settings.gigachat_model,
            "messages": history,
            "temperature": 0.7,
        }

        try:
            response = await self._client.post(self.completion_url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            logger.error("gigachat_completion_failed", error=str(e))
            # Fallback на случай ошибки API
            return "Извините, у меня сейчас технические неполадки. София скоро ответит лично! 🙏"

    async def close(self):
        await self._client.aclose()

# Глобальный синглтон
gigachat_client = GigaChatClient()