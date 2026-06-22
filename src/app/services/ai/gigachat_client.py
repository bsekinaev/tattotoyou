# src/app/services/ai/gigachat_client.py
"""
Клиент для работы с API Сбер GigaChat.
С distributed token caching через Redis.
"""
import base64
import uuid
import warnings
import httpx
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.logging import get_logger

# У Сбера специфические сертификаты, для MVP отключаем строгую проверку SSL
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = get_logger(__name__)
settings = get_settings()


class GigaChatClient:
    """
    Асинхронный клиент для GigaChat API с distributed token caching.

    Архитектурные решения:
    1. Токен кэшируется в Redis (TTL 30 мин) — все воркеры переиспользуют один токен
    2. Fallback на in-memory cache если Redis недоступен (graceful degradation)
    3. SSL verification отключена (специфика сертификатов Сбера)
    """

    # Ключ для хранения токена в Redis
    _TOKEN_KEY = "gigachat:access_token"
    # TTL токена в секундах (30 минут). Токены Сбера живут ~1 час, 30 мин — безопасный буфер
    _TOKEN_TTL = 1800

    def __init__(self, redis_client: Redis | None = None):
        self.auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        self.completion_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        self._client = httpx.AsyncClient(verify=False, timeout=30.0)
        self._redis = redis_client

        # Fallback: in-memory cache на случай если Redis недоступен
        self._access_token: str | None = None

    async def _get_token(self) -> str:
        """
        Получение OAuth-токена с distributed caching.

        Flow:
        1. Проверяем Redis (быстро, ~1мс)
        2. Если нет — запрашиваем у Сбера (~300мс)
        3. Сохраняем в Redis для других воркеров
        """
        # ========================================
        # ШАГ 1: Пробуем взять из Redis-кэша
        # ========================================
        if self._redis:
            try:
                cached_token = await self._redis.get(self._TOKEN_KEY)
                if cached_token:
                    logger.debug("gigachat_token_from_cache")
                    return cached_token
            except Exception as e:
                # Graceful degradation: если Redis упал, идём к Сберу
                logger.warning("redis_cache_read_failed", error=str(e))

        # ========================================
        # ШАГ 2: Fallback на in-memory cache
        # ========================================
        if self._access_token:
            logger.debug("gigachat_token_from_memory")
            return self._access_token

        # ========================================
        # ШАГ 3: Запрашиваем новый токен у Сбера
        # ========================================
        credentials = (
            f"{settings.gigachat_client_id.get_secret_value()}:"
            f"{settings.gigachat_client_secret.get_secret_value()}"
        )
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
            new_token = response.json()["access_token"]

            logger.info("gigachat_token_acquired")

            # Сохраняем в in-memory cache
            self._access_token = new_token

            # ========================================
            # ШАГ 4: Кэшируем в Redis для других воркеров
            # ========================================
            if self._redis:
                try:
                    await self._redis.set(
                        self._TOKEN_KEY,
                        new_token,
                        ex=self._TOKEN_TTL
                    )
                    logger.info(
                        "gigachat_token_cached",
                        ttl_seconds=self._TOKEN_TTL
                    )
                except Exception as e:
                    logger.warning("redis_cache_write_failed", error=str(e))

            return new_token

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
            response = await self._client.post(
                self.completion_url,
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            logger.error("gigachat_completion_failed", error=str(e))
            # Fallback на случай ошибки API
            return "Извините, у меня сейчас технические неполадки. София скоро ответит лично! 🙏"

    async def close(self):
        """Корректное закрытие HTTP-сессии."""
        await self._client.aclose()


# Глобальный синглтон (используется в FastAPI-контексте, без Redis)
# В Celery-воркерах создаём свежие инстансы с Redis-клиентом
gigachat_client = GigaChatClient()