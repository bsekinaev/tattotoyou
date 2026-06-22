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
**Решение:** FastAPI выступает в роли Thin Controller (HMAC верификация -> Rate Limit -> Deduplication -> `task.delay()`). Тяжелая бизнес-логика выполняется в Celery-воркерах с `task_acks_late` и `autoretry_for` (exponential backoff), что гарантирует **at-least-once delivery** даже при падении API Сбера.

### 2. Distributed Token Caching
OAuth-токены GigaChat (TTL ~1 часа) кэшируются в Redis с буфером в 30 минут. Это позволяет горизонтально масштабировать Celery-воркеры в Docker-кластере без лишних запросов к auth-серверу и снижает latency ответа на ~300мс.

### 3. Atomic Rate Limiting (Lua + Redis)
Для защиты бюджета LLM от спамеров реализован Fixed Window Counter на **Lua-скриптах** внутри Redis. Это гарантирует атомарность операций `INCR` + `EXPIRE`, исключая Race Conditions и утечку памяти, свойственные наивным реализациям через Pipeline.

### 4. Graceful Degradation & Fallback
Если GigaChat API недоступен (исчерпаны 3 ретрая Celery), система не бросает клиента. Срабатывает `FallbackResponder`, который отправляет вежливое шаблонное сообщение, а в логах появляется алерт для администратора.

## 🧠 AI & Business Logic
- **Intent Classification:** Быстрый keyword-based роутинг (с использованием substring-матчинга для покрытия русской морфологии) без траты токенов LLM.
- **Escalation Engine:** Перехватывает токсичные и медицинские триггеры до вызова LLM.
- **Context Builder:** Динамически инжектит имя клиента и VIP-статус в System Prompt для персонализации.
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
Применён **риск-ориентированный подход**. 100% покрытие критичной бизнес-логики (`IntentClassifier`, `EscalationEngine`, `PromptBuilder`, `RateLimiter`) с использованием `pytest.mark.parametrize` и моков Redis. Инфраструктурный код покрыт интеграционными тестами.

```bash
# Запуск тестов с проверкой покрытия
python -m pytest tests/unit -v --cov=src/app
```

## 🚀 Локальный запуск

1. Клонируйте репозиторий и создайте `.env` на основе `.env.example`.
2. Поднимите инфраструктуру (PostgreSQL + Redis):
   ```bash
   docker compose up -d
   ```
3. Установите зависимости и примените миграции:
   ```bash
   python -m pip install -e ".[dev]"
   alembic upgrade head
   ```
4. Запустите API и Worker (в разных терминалах):
   ```bash
   # Терминал 1: API
   python -m uvicorn app.main:app --reload --reload-dir src
   
   # Терминал 2: Celery Worker
   python -m celery -A app.workers.celery_app worker --loglevel=info --pool=solo
   ```

## 👨‍💻 Автор
**Батраз Секинаев**  
Python Backend Developer  
[GitHub](https://github.com/bsekinaev) | [Telegram](https://t.me/bsekinaev)
