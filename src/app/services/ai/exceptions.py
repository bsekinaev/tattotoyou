"""Типизированные безопасные ошибки интеграции с GigaChat."""

from __future__ import annotations


class GigaChatError(RuntimeError):
    """Базовая ошибка GigaChat без секретов и содержимого HTTP-ответа."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class GigaChatAuthenticationError(GigaChatError):
    """OAuth-токен или клиентские credentials отклонены."""


class GigaChatRateLimitError(GigaChatError):
    """GigaChat временно ограничил частоту запросов."""


class GigaChatTimeoutError(GigaChatError):
    """Истёк таймаут обращения к GigaChat."""


class GigaChatTransportError(GigaChatError):
    """Сетевая ошибка до получения HTTP-ответа."""


class GigaChatServerError(GigaChatError):
    """Временная серверная ошибка GigaChat."""


class GigaChatInvalidResponseError(GigaChatError):
    """GigaChat вернул успешный, но непригодный к использованию ответ."""
