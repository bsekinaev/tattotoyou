"""Настройка безопасного структурированного логирования приложения."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from app.core.config import get_settings

_REDACTED = "[REDACTED]"

# Поля, которые никогда не должны попадать в логи в открытом виде.
_SECRET_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "credential",
    "dsn",
)

# Пользовательские идентификаторы и содержимое сообщений считаются PII.
_PII_KEYS = frozenset(
    {
        "chat_id",
        "user_id",
        "external_id",
        "client_name",
        "client_username",
        "username",
        "display_name",
        "phone",
        "phone_number",
        "email",
        "error",
        "ip",
        "ip_address",
        "text",
        "text_snippet",
        "last_message",
        "message_text",
        "content",
        "query",
        "payload",
    }
)

# Даже безопасное поле ``event`` или traceback может случайно содержать секрет.
_TELEGRAM_TOKEN_RE = re.compile(
    r"(?i)(https?://api\.telegram\.org/bot)[^/\s]+",
)
_URL_CREDENTIALS_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@\s]+(@)")
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key|client[_-]?secret)\s*[:=]\s*[^\s,;]+"
)
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?7|8)[\s()-]*\d{3}[\s()-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\w)")


def sanitize_log_text(value: str) -> str:
    """Удалить секреты и очевидные персональные данные из произвольного текста."""
    sanitized = _TELEGRAM_TOKEN_RE.sub(r"\1[REDACTED]", value)
    sanitized = _URL_CREDENTIALS_RE.sub(r"\1[REDACTED]\2", sanitized)
    sanitized = _BEARER_RE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={_REDACTED}", sanitized)
    sanitized = _EMAIL_RE.sub(_REDACTED, sanitized)
    sanitized = _PHONE_RE.sub(_REDACTED, sanitized)
    return sanitized


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _sanitize_value(key: str, value: Any) -> Any:
    """Рекурсивно очистить значение структурированного поля."""
    normalized_key = key.lower()
    if normalized_key in _PII_KEYS or _is_secret_key(normalized_key):
        return _REDACTED

    if isinstance(value, str):
        return sanitize_log_text(value)

    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_value(str(item_key), item_value)
            for item_key, item_value in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_value(key, item) for item in value]

    return value


def redact_sensitive_data(
    _logger: WrappedLogger,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Structlog processor, удаляющий секреты и PII перед рендерингом."""
    return {key: _sanitize_value(key, value) for key, value in event_dict.items()}


def setup_logging() -> None:
    """Инициализировать structlog и стандартный logging."""
    settings = get_settings()

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_sensitive_data,
    ]

    if settings.debug:
        renderer: Processor = structlog.dev.ConsoleRenderer(
            colors=True,
            sort_keys=True,
        )
    else:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            *shared_processors,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, settings.log_level))

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Получить именованный structlog-логгер."""
    return structlog.get_logger(name or __name__)
