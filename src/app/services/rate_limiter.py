import time

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)

# 🛡️ Lua-скрипт для атомарного Fixed Window Counter
# Это решает проблему Race Condition и "скользящего" TTL
LUA_FIXED_WINDOW = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RateLimiter:
    def __init__(self, redis_client: Redis, max_requests: int = 10, window_seconds: int = 60):
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._sha = None  # Кэш SHA-скрипта для производительности

    async def _get_script_sha(self) -> str:
        if not self._sha:
            self._sha = await self.redis.script_load(LUA_FIXED_WINDOW)
        return self._sha

    async def is_allowed(self, user_id: str) -> tuple[bool, int]:
        """
        Проверяет, не превышен ли лимит.
        Возвращает: (разрешено ли, сколько осталось попыток)
        """
        # Ключ включает в себя окно времени (например, :14:35), чтобы окна не пересекались
        window_key = int(time.time() // self.window_seconds)
        key = f"rate_limit:{user_id}:{window_key}"

        try:
            sha = await self._get_script_sha()
            current_requests = await self.redis.evalsha(sha, 1, key, self.window_seconds)

            allowed = current_requests <= self.max_requests
            remaining = max(0, self.max_requests - current_requests)

            if not allowed:
                logger.warning("rate_limit_exceeded", user_id=user_id, current=current_requests)

            return allowed, remaining

        except Exception as e:
            # Graceful degradation: если Redis упал, не блокируем пользователя
            logger.error("rate_limiter_failed", error=str(e))
            return True, -1
