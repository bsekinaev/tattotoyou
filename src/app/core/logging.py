"""
Настройка structlog для структурированного логирования.
"""

import logging
import sys

import structlog
from structlog.types import Processor

from app.core.config import get_settings


def setup_logging() -> None:
    """Инициализация логгера приложения."""
    settings = get_settings()

    # Базовые процессоры, которые точно есть во всех версиях
    shared_processors: list[Processor] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # ============================================
    # ВЫБОР РЕНДЕРЕРА (ФОРМАТА ВЫВОДА)
    # ============================================
    if settings.debug:
        # В локалке — читаемый цветной вывод в консоль
        # sort_keys=True — сортирует ключи по алфавиту для удобства чтения
        renderer: Processor = structlog.dev.ConsoleRenderer(
            colors=True,
            sort_keys=True,
        )
    else:
        # В проде — JSON для машин (ELK, Loki, Datadog)
        # sort_keys=True — детерминированный JSON (удобно для тестов и diff)
        renderer = structlog.processors.JSONRenderer(sort_keys=True)

    # ============================================
    # КОНФИГУРАЦИЯ STRUCTLOG
    # ============================================
    structlog.configure(
        processors=[
            # 1. Фильтруем по уровню (чтобы DEBUG не попал в INFO)
            structlog.stdlib.filter_by_level,
            # 2. Добавляем стандартные поля
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            # 3. Наши общие процессоры (timestamp, exc_info и т.д.)
            *shared_processors,
            # 4. Декодируем байты в строки (защита от UnicodeDecodeError)
            structlog.processors.UnicodeDecoder(),
            # 5. Передаём управление стандартному logging
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,  # Кэшируем логгеры для производительности
    )

    # ============================================
    # ИНТЕГРАЦИЯ СО СТАНДАРТНЫМ LOGGING
    # ============================================
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,  # <-- Сюда идёт наш выбранный рендерер (Console или JSON)
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, settings.log_level))

    # ============================================
    # УМЕНЬШАЕМ ШУМ ОТ БИБЛИОТЕК
    # ============================================
    # Эти библиотеки логируют слишком много (каждый HTTP-запрос, каждый SQL)
    # В проде мы хотим видеть только ВАЖНЫЕ логи нашего приложения
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiokafka").setLevel(logging.WARNING)


def get_logger(name: str | None = None):
    """
    Получить логгер для модуля.

    Использование:
        from app.core.logging import get_logger
        logger = get_logger(__name__)
        logger.info("user logged in", user_id=123, ip="1.2.3.4")
    """
    return structlog.get_logger(__name__)
