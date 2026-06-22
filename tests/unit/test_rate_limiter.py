import pytest
from unittest.mock import AsyncMock
from app.services.rate_limiter import RateLimiter

class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_is_allowed_under_limit(self):
        mock_redis = AsyncMock()
        mock_redis.evalsha = AsyncMock(return_value=5)  # 5 запросов сделано
        mock_redis.script_load = AsyncMock(return_value="sha123")

        limiter = RateLimiter(redis_client=mock_redis, max_requests=10, window_seconds=60)
        allowed, remaining = await limiter.is_allowed("user_123")

        assert allowed is True
        assert remaining == 5
        mock_redis.evalsha.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_blocked_over_limit(self):
        mock_redis = AsyncMock()
        mock_redis.evalsha = AsyncMock(return_value=11)  # Лимит превышен
        mock_redis.script_load = AsyncMock(return_value="sha123")

        limiter = RateLimiter(redis_client=mock_redis, max_requests=10, window_seconds=60)
        allowed, remaining = await limiter.is_allowed("user_123")

        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_redis_failure(self):
        """
        Критичный тест: если Redis упал, мы НЕ блокируем пользователей (fail-open).
        """
        mock_redis = AsyncMock()
        mock_redis.evalsha = AsyncMock(side_effect=Exception("Redis connection refused"))
        mock_redis.script_load = AsyncMock(return_value="sha123")

        limiter = RateLimiter(redis_client=mock_redis, max_requests=10, window_seconds=60)
        allowed, remaining = await limiter.is_allowed("user_123")

        assert allowed is True  # Разрешаем запрос
        assert remaining == -1  # Флаг, что лимит неизвестен