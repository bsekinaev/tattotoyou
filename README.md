# TATTOTOYOU — AI-ассистент для тату-студии

[![CI](https://github.com/bsekinaev/tattotoyou/actions/workflows/ci.yml/badge.svg)](https://github.com/bsekinaev/tattotoyou/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?logo=postgresql&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-5.3+-37814A?logo=celery&logoColor=white)

Telegram-ассистент для обработки типовых обращений клиентов тату-студии. Сервис принимает webhook-события, определяет сценарий обращения, формирует ответ с помощью GigaChat и передаёт сложные или чувствительные случаи мастеру.

> **Статус:** portfolio MVP в стадии стабилизации надёжности. PostgreSQL Inbox и идемпотентная обработка входящих событий реализованы; Transactional Outbox и гарантированная исходящая доставка остаются в roadmap.

## Задача проекта

Тату-мастеру регулярно поступают повторяющиеся вопросы о стоимости, стилях, подготовке и уходе. TATTOTOYOU автоматизирует первичную обработку таких обращений, при этом не пытается самостоятельно решать медицинские и конфликтные ситуации.

Проект демонстрирует:

- событийную обработку Telegram webhook'ов;
- фоновые задачи и взаимодействие с внешним AI API;
- хранение данных в PostgreSQL;
- rate limiting, дедупликацию и защиту чувствительных данных;
- тестирование, контейнеризацию и автоматические проверки.

## Реализовано

- приём Telegram webhook-событий через FastAPI;
- проверка Telegram Secret Token;
- долговечная фиксация входящих событий в PostgreSQL до постановки в Celery;
- идемпотентная обработка повторных Telegram update и recovery зависших событий;
- интеграция с GigaChat по OAuth2;
- основа базы знаний и pgvector; подключение RAG к активному pipeline находится в roadmap;
- keyword-based классификация типовых намерений;
- эскалация медицинских, токсичных и нестандартных запросов мастеру;
- Redis rate limiter на Lua-скрипте;
- маскирование телефонов и email перед вызовом внешнего AI API;
- обязательная TLS-верификация для GigaChat;
- `/live`, `/ready` и `/health` для проверки состояния сервиса;
- структурированное логирование;
- миграции Alembic;
- Docker Compose и GitHub Actions.

## Архитектура

```mermaid
flowchart LR
    TG[Telegram] -->|Webhook| API[FastAPI]
    API --> SEC[Secret token / rate limit]
    SEC --> INBOX[(PostgreSQL Inbox)]
    INBOX --> Q[Redis / Celery queue]
    BEAT[Celery Beat recovery] --> INBOX
    BEAT --> Q
    Q --> W[Celery worker]
    W --> DB[(Clients / conversations / messages)]
    W -.-> RAG[Knowledge/RAG foundation]
    W --> AI[GigaChat API]
    W --> OUT[Telegram response / escalation]
```

Приложение разделено на API-слой, сервисы, репозитории и фоновые задачи. FastAPI валидирует событие, атомарно сохраняет его в PostgreSQL Inbox и только после commit пытается передать UUID события Celery-воркеру. Ошибка брокера не теряет update: Celery Beat повторно ставит pending/retrying и зависшие processing-события в очередь.

## Ключевые инженерные решения

### Асинхронная обработка webhook'ов

Вызов внешнего AI API может занимать заметное время. Webhook-обработчик не ожидает генерацию ответа, а ставит задачу в Celery. Это сокращает время удержания HTTP-соединения и отделяет транспортный слой от бизнес-логики.

### Rate limiting до очереди

Ограничение частоты применяется до `task.delay()`. Счётчик реализован через Lua в Redis, поэтому изменение значения и установка TTL выполняются атомарно.

### PostgreSQL Inbox и идемпотентность

Пара `(platform, external_event_id)` уникальна, поэтому повторная доставка Telegram update возвращает существующее событие. Воркер захватывает событие через `FOR UPDATE SKIP LOCKED`, увеличивает счётчик попыток и переводит его в `processing`. Ошибки переводят событие в `retrying` с exponential backoff, а превышение лимита — в `failed`.

Каждый Inbox event может породить максимум одно входящее и одно исходящее сообщение благодаря частичному уникальному индексу `(causation_event_id, direction)`. Это устраняет повторную запись бизнес-ответа при повторном запуске Celery-задачи. Гарантия фактической доставки в Telegram будет реализована отдельным Transactional Outbox.

### Работа с чувствительными данными

Перед передачей истории внешнему AI-провайдеру телефоны и email заменяются безопасными маркерами. Оригинальный текст остаётся в собственной PostgreSQL. Непроверенные данные профиля изолируются от системных инструкций.

### Отказоустойчивость внешней интеграции

Celery поддерживает повтор задач, а GigaChat имеет безопасный fallback-ответ. Классификация временных ошибок внешнего API, полная идемпотентность на PostgreSQL и гарантированная исходящая доставка развиваются отдельно и явно отмечены в roadmap.

## Технологический стек

| Область | Технологии |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Database | PostgreSQL 15, SQLAlchemy 2.0 async, Alembic |
| Queue / cache | Celery, Redis |
| AI | GigaChat API, OAuth2, pgvector/RAG foundation |
| HTTP | httpx |
| Tests | pytest, pytest-asyncio, pytest-cov |
| Quality | Ruff, GitHub Actions |
| Infrastructure | Docker, Docker Compose |
| Logging | structlog |

## Быстрый запуск

### 1. Подготовка конфигурации

```bash
cp .env.example .env
```

Заполните обязательные переменные для PostgreSQL, Redis, Telegram, GigaChat и Admin API. Секреты и сертификаты не должны попадать в репозиторий. Безопасно проверить заполнение можно командой `python scripts/check_configuration.py`; значения секретов она не выводит.

### 2. Запуск полного стека

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up --build
```

### 3. Миграции

```bash
python -m alembic upgrade head
```

### 4. Локальный запуск API и worker

```bash
python -m uvicorn app.main:app --reload --reload-dir src
```

```bash
python -m celery -A app.workers.celery_app:celery_app worker --loglevel=info --pool=solo
```

В отдельном терминале запустите recovery-планировщик:

```bash
python -m celery -A app.workers.celery_app:celery_app beat --loglevel=info
```

`--pool=solo` используется для локального запуска worker на Windows. В Linux можно использовать стандартный prefork pool.

## Проверка состояния

```text
GET /live   # процесс приложения работает
GET /ready  # PostgreSQL и Redis доступны
GET /health # совместимый alias для /ready
```

Readiness-ответ не раскрывает внутренние тексты инфраструктурных исключений.

## Тестирование

```bash
python -m pytest tests/unit -v --cov=src/app
```

Unit-тесты проверяют классификацию запросов, эскалацию, изоляцию prompt metadata, аутентификацию Admin API, webhook secret, ограничение тела webhook до JSON-парсинга, TLS, минимизацию PII, PostgreSQL Inbox и health endpoints.

Интеграционные проверки PostgreSQL и конкурентных инвариантов запускаются отдельно на реальной тестовой базе.

## Безопасность

- тело Telegram webhook ограничивается до JSON/Pydantic-парсинга;
- Telegram Secret Token проверяется до бизнес-обработки события;
- Admin API принимает ключ через заголовок `X-Admin-Key`;
- TLS-проверка внешнего AI API не отключается;
- сертификаты и секреты не коммитятся;
- базовый Docker Compose не публикует PostgreSQL и Redis наружу;
- исходящие HTTP-клиенты не наследуют случайную системную proxy-конфигурацию по умолчанию.

## Ограничения и roadmap

- [x] Telegram webhook и Celery pipeline
- [x] PostgreSQL, Redis и Alembic
- [x] GigaChat OAuth2 и pgvector foundation
- [ ] подключение RAG к активному pipeline и lifecycle embeddings
- [x] rate limiting и PII-redaction
- [x] health endpoints и CI
- [x] PostgreSQL Inbox и идемпотентная обработка входящих событий
- [ ] Transactional Outbox, гарантированная исходящая доставка и outbound retry
- [ ] административный интерфейс оператора
- [ ] расширенные интеграционные и нагрузочные тесты
- [ ] метрики и dashboard observability

## Автор

**Батраз Секинаев** — Python Backend Developer

[GitHub](https://github.com/bsekinaev) · [Telegram](https://t.me/bsekinaev)