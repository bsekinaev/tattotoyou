"""
Абстрактный интерфейс для всех платформ (Telegram, VK, Instagram).

Архитектурное решение: Platform Adapter Pattern
Позволяет бизнес-логике (ConversationService) работать с любой платформой,
не зная её деталей реализации.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class PlatformUser:
    """Универсальное представление пользователя (platform-agnostic)."""
    external_id: str  # ID в соцсети (telegram user_id, vk user_id, etc)
    display_name: str | None = None
    username: str | None = None
    is_bot: bool = False


@dataclass
class PlatformMessage:
    """Универсальное представление сообщения (platform-agnostic)."""
    platform: str  # "telegram", "vk", "instagram"
    message_id: str  # ID сообщения в платформе
    chat_id: str  # ID чата/беседы в платформе
    user: PlatformUser
    text: str | None = None
    timestamp: datetime | None = None

    # Опциональные поля для медиа
    attachments: list[dict] | None = None


class PlatformAdapter(ABC):
    """
    Абстрактный адаптер для работы с платформой.

    Каждая платформа (Telegram, VK, Instagram) реализует этот интерфейс.
    ConversationService работает только с этим интерфейсом, не зная деталей.
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Название платформы: 'telegram', 'vk', 'instagram'."""
        pass

    @abstractmethod
    async def send_message(self, chat_id: str, text: str) -> str:
        """
        Отправить текстовое сообщение.

        Args:
            chat_id: ID чата в платформе
            text: Текст сообщения (может содержать HTML/Markdown)

        Returns:
            message_id: ID отправленного сообщения в платформе

        Raises:
            PlatformError: если отправка не удалась
        """
        pass

    @abstractmethod
    async def verify_webhook(self, request_data: dict, headers: dict) -> bool:
        """
        Верифицировать webhook запрос от платформы.

        Args:
            request_data: Raw JSON payload от платформы
            headers: HTTP headers запроса

        Returns:
            True если запрос валидный, False иначе
        """
        pass

    @abstractmethod
    async def parse_message(self, request_data: dict) -> PlatformMessage | None:
        """
        Распарсить входящий webhook в универсальное PlatformMessage.

        Args:
            request_data: Raw JSON payload от платформы

        Returns:
            PlatformMessage если это сообщение, None если системное событие
        """
        pass

    async def close(self) -> None:
        """Закрыть HTTP-клиент (если есть)."""
        pass