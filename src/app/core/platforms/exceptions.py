"""Безопасные исключения платформенных адаптеров.

Исключения внешних HTTP-клиентов могут содержать полный URL запроса. Для
Telegram URL включает bot token, поэтому наружу из адаптера пробрасываются
только нормализованные исключения без секретов и содержимого ответа.
"""


class PlatformError(RuntimeError):
    """Базовая ошибка взаимодействия с внешней платформой."""

    def __init__(
        self,
        message: str,
        *,
        platform: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.platform = platform
        self.status_code = status_code


class PlatformHTTPError(PlatformError):
    """Платформа вернула HTTP-ответ с ошибочным статусом."""


class PlatformTransportError(PlatformError):
    """Запрос не дошёл до платформы из-за сетевой ошибки."""
