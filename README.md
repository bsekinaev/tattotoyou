# 🎨 TATTOTOYOU — AI-ассистент для тату-студии

[![CI](https://github.com/bsekinaev/tattotoyou/actions/workflows/ci.yml/badge.svg)](https://github.com/bsekinaev/tattotoyou/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)
![Celery](https://img.shields.io/badge/Celery-5.3+-37B24D.svg)
![Postgres](https://img.shields.io/badge/PostgreSQL-15-336791.svg)

Мультиплатформенный AI-агент, который обрабатывает рутинные диалоги с клиентами, квалифицирует лиды и эскалирует сложные кейсы на мастера. Спроектирован с упором на отказоустойчивость, безопасность и продуктовое мышление.

## 🎯 Бизнес-ценность
- **Экономия времени мастера:** ИИ берет на себя 70%+ типовых вопросов (цены, уход, стили).
- **Защита от выгорания:** Автоматическая фильтрация спама и нецелевых запросов.
- **Безопасность (Risk Management):** Жесткие правила эскалации медицинских вопросов (диабет, беременность) и жалоб напрямую к человеку. Исключает юридические и репутационные риски.

## 🏗 Архитектура и Key Engineering Decisions

Проект реализован на принципах **Clean Architecture** и **Event-Driven Design**.

### 1. Event-Driven Pipeline (FastAPI + Redis + Celery)
Webhook от Telegram требует ответа `200 OK` за доли секунды. Синхронный вызов LLM (2-5 сек) приводит к таймаутам и спаму ретраями.
**Решение:** FastAPI выступает в роли Thin Controller (проверка Telegram Secret Token → Rate Limit → Redis-дедупликация → `task.delay()`). Тяжёлая бизнес-логика выполняется в Celery-воркерах с `task_acks_late` и retry/backoff. Полная идемпотентность на уровне PostgreSQL и надёжный outbound delivery находятся в этапе стабилизации.

### 2. Distributed Token Caching
OAuth-токены GigaChat (TTL ~1 часа) кэшируются в Redis с буфером в 30 минут. Это позволяет горизонтально масштабировать Celery-воркеры в Docker-кластере без лишних запросов к auth-серверу и снижает latency ответа на ~300мс.

### 3. Atomic Rate Limiting (Lua + Redis)
Для защиты бюджета LLM от спамеров реализован Fixed Window Counter на **Lua-скриптах** внутри Redis. Это гарантирует атомарность операций `INCR` + `EXPIRE`, исключая Race Conditions и утечку памяти, свойственные наивным реализациям через Pipeline.

### 4. Graceful Degradation & Fallback
Для недоступности GigaChat предусмотрен `FallbackResponder`. Классификация временных и постоянных ошибок, гарантированное уведомление администратора и отдельный outbound retry будут завершены на этапе Delivery Reliability.

## 🧠 AI & Business Logic
- **Intent Classification:** Быстрый keyword-based роутинг (с использованием substring-матчинга для покрытия русской морфологии) без траты токенов LLM.
- **Escalation Engine:** Перехватывает токсичные и медицинские триггеры до вызова LLM.
- **Context Builder:** Изолирует проверенное имя клиента в отдельном блоке недоверенных метаданных; произвольные строки профиля не добавляются в основной System Prompt.
- **Admin Notifications:** Асинхронная отправка карточек эскалаций в закрытый Telegram-канал мастера через отдельную Celery-задачу.

## 🛠 Стек технологий
- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (Async)
- **Task Queue:** Celery, Redis (Broker + Cache + Rate Limiter)
- **Database:** PostgreSQL 15, Alembic (Migrations)
- **AI:** GigaChat API (OAuth2)
- **Testing:** pytest, pytest-asyncio, pytest-cov, AsyncMock
- **CI/CD & Quality:** GitHub Actions, Ruff (Linter + Formatter)
- **Observability:** structlog (JSON/Console)

## 🧪 Тестирование
Применён **риск-ориентированный подход**: unit-тесты покрывают классификацию, эскалацию, prompt metadata isolation, Admin API authentication, webhook secret handling, TLS, PII-redaction и health endpoints. Интеграционные тесты PostgreSQL/Redis/Celery входят в следующий этап стабилизации.

```bash
# Запуск unit-тестов с проверкой покрытия
python -m pytest tests/unit -v --cov=src/app
```

Конкурентные инварианты репозиториев проверяются на реальном PostgreSQL после
применения миграций. Тест использует уникальные данные и удаляет их после запуска:

```powershell
$env:TEST_POSTGRES_DSN = python -c "from app.core.config import get_settings; print(get_settings().postgres_dsn)"
python -m pytest tests/integration/test_repository_concurrency.py -v
Remove-Item Env:TEST_POSTGRES_DSN
```

## 🚀 Локальный запуск

1. Создайте локальную конфигурацию и замените все placeholder-секреты:

   ```bash
   cp .env.example .env
   ```

   `ADMIN_API_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, Telegram и GigaChat credentials обязательны. Admin API принимает ключ только через заголовок `X-Admin-Key`.

   GigaChat использует обязательную TLS-верификацию. Установите корневой сертификат НУЦ Минцифры в системное хранилище или укажите путь к PEM-файлу через `GIGACHAT_CA_BUNDLE`. Отключение проверки сертификатов в коде не поддерживается.
   Для Docker путь должен существовать внутри контейнера: смонтируйте PEM-файл read-only и задайте, например, `GIGACHAT_CA_BUNDLE=/run/secrets/gigachat-ca.pem`. Сам сертификат и приватные секреты не коммитятся в репозиторий.

   Исходящие HTTP-клиенты по умолчанию не наследуют системные `HTTP_PROXY`, `HTTPS_PROXY` и `ALL_PROXY` (`HTTP_TRUST_ENV=false`). Это защищает запуск от случайной или неподдерживаемой proxy-конфигурации операционной системы.

2. Выберите режим локального запуска.

   **Полный стек в Docker:**

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f docker-compose.dev.yml \
     up --build
   ```

   **API и Celery в `.venv`, инфраструктура в Docker:**

   ```bash
   docker compose \
     -f docker-compose.yml \
     -f docker-compose.dev.yml \
     up -d postgres redis

   python scripts/check_infrastructure.py
   python -m alembic upgrade head
   ```

   Для этого режима `.env` должен содержать:

   ```env
   POSTGRES_HOST=localhost
   POSTGRES_PORT=5433
   REDIS_HOST=localhost
   REDIS_PORT=6380
   ```

   Пароли `POSTGRES_PASSWORD` и `REDIS_PASSWORD` должны совпадать с теми,
   с которыми запущен Docker Compose. Celery не может стартовать без Redis.

   После успешного preflight:

   ```bash
   python -m uvicorn app.main:app --reload --reload-dir src \
     --reload-exclude "**/__pycache__/**" --reload-exclude "*.pyc"

   python -m celery -A app.workers.celery_app:celery_app \
     worker --loglevel=info --pool=solo
   ```

   В локальном режиме `STARTUP_REQUIRE_DEPENDENCIES=false`: HTTP-процесс
   поднимается даже при недоступной инфраструктуре, `/live` возвращает 200,
   а `/ready` — 503. Docker Compose принудительно включает строгий startup.

   Dev override публикует только loopback-порты: API `8000`, PostgreSQL `5433`,
   Redis `6380`. Базовый `docker-compose.yml` не публикует PostgreSQL и Redis наружу.

   Проверки состояния API:

   ```text
   GET /live   # процесс приложения работает
   GET /ready  # PostgreSQL и Redis доступны
   GET /health # совместимый alias для /ready
   ```

   Readiness-ответы возвращают только `ok/error` и не раскрывают тексты
   инфраструктурных исключений.

   Перед применением Git-патчей или массовой генерацией файлов остановите Uvicorn
   с `--reload`. Иначе WatchFiles может показать сотни событий delete/add из-за
   атомарной замены файлов и пересоздания `__pycache__`; это не означает удаление
   исходников.


3. Проверка репозитория:

   ```bash
   python scripts/validate_repository.py
   ```

   До появления `uv.lock` установите зависимости явно:

   ```bash
   uv sync --extra dev --extra rag
   ```

## 👨‍💻 Автор
**Батраз Секинаев**  
Python Backend Developer  
[GitHub](https://github.com/bsekinaev) | [Telegram](https://t.me/bsekinaev)