"""
Конфигурация приложения через Pydantic Settings.
Bulletproof-версия с явной загрузкой .env через python-dotenv.
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================
# ПОИСК КОРНЯ ПРОЕКТА
# ============================================
def find_project_root() -> Path:
    """Ищем корень проекта по pyproject.toml."""
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    # Fallback: 4 уровня вверх
    return current.parent.parent.parent.parent


PROJECT_ROOT = find_project_root()
ENV_FILE = PROJECT_ROOT / ".env"


# ============================================
# ЯВНАЯ ЗАГРУЗКА .env (критично для Windows!)
# ============================================
# Это нужно сделать ПЕРЕД определением класса Settings,
# чтобы переменные уже были в os.environ к моменту валидации.
if ENV_FILE.exists():
    # override=False — не перезаписываем реальные env-переменные из системы
    load_dotenv(dotenv_path=ENV_FILE, override=False)
else:
    # В проде .env может не быть, используем env vars контейнера
    print(f"⚠️  .env file not found at {ENV_FILE}, using system env vars")


class Settings(BaseSettings):
    """Основные настройки приложения."""

    # ============================================
    # APPLICATION
    # ============================================
    app_name: str = "Tattoo Assistant"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"

    # ============================================
    # TELEGRAM (обязательные)
    # ============================================
    telegram_bot_token: SecretStr = Field(
        description="Токен бота от @BotFather"
    )
    telegram_webhook_secret: SecretStr = Field(
        description="Секрет для верификации webhook'ов Telegram"
    )
    telegram_admin_chat_id: int = Field(
        description="Chat ID Софии для уведомлений об эскалациях"
    )

    # ============================================
    # VK (опционально для MVP)
    # ============================================
    vk_access_token: SecretStr | None = None
    vk_group_id: int | None = None
    vk_api_version: str = "5.199"
    vk_webhook_secret: SecretStr | None = None

    # ============================================
    # INSTAGRAM (опционально)
    # ============================================
    ig_access_token: SecretStr | None = None
    ig_app_secret: SecretStr | None = None
    ig_page_id: int | None = None

    # ============================================
    # GIGACHAT (обязательные)
    # ============================================
    gigachat_client_id: SecretStr = Field(
        description="Client ID из кабинета Сбер GigaChat API"
    )
    gigachat_client_secret: SecretStr = Field(
        description="Client Secret"
    )
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-Pro"

    # ============================================
    # DATABASE
    # ============================================
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "tattoo_assistant"
    postgres_user: str = "tattoo_user"
    postgres_password: SecretStr = Field(
        description="Пароль от PostgreSQL"
    )

    # ============================================
    # REDIS
    # ============================================
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: SecretStr | None = None

    # ============================================
    # SECURITY
    # ============================================
    secret_key: SecretStr = Field(
        description="Секретный ключ для HMAC"
    )

    # ============================================
    # OBSERVABILITY
    # ============================================
    sentry_dsn: str | None = None

    # ============================================
    # COMPUTED PROPERTIES
    # ============================================

    @property
    def postgres_dsn(self) -> str:
        """DSN для asyncpg."""
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        """DSN для Alembic (синхронный)."""
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """URL для Redis client."""
        if self.redis_password:
            password = self.redis_password.get_secret_value()
            return f"redis://:{password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ============================================
    # VALIDATORS
    # ============================================

    @field_validator(
        # Только опциональные SecretStr поля:
        "vk_access_token",
        "vk_webhook_secret",
        "ig_access_token",
        "ig_app_secret",
        "redis_password",
        "sentry_dsn",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, v: str | None) -> str | None:
        """
        Конвертируем пустые строки в None ТОЛЬКО для опциональных полей.

        Для обязательных полей (telegram_bot_token и т.д.) это НЕ делаем,
        чтобы получить понятную ошибку "missing" вместо "string_type".
        """
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    # Только для опциональных int полей
    @field_validator(
        "vk_group_id",
        "ig_page_id",
        mode="before",
    )
    @classmethod
    def empty_int_to_none(cls, v: str | int | None) -> int | None:
        """Обрабатываем пустые строки для опциональных int-полей."""
        if isinstance(v, str) and v.strip() == "":
            return None
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v.upper()

    # ============================================
    # PYDANTIC CONFIG (оставляем на всякий случай)
    # ============================================
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton для настроек."""
    return Settings()