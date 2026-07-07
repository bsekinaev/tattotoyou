# Repository validation baseline

Date: 2026-07-08
Patch: `AI Quality + Escalation Hardening`

Этот документ фиксирует проверяемую инженерную базу после крупного AI/safety
патча. Production Gate ранее подтверждён зелёным GitHub Actions; текущий патч
должен повторно пройти те же quality, PostgreSQL integration и container gates
после применения в основном репозитории.

## Активный execution path

```text
POST /webhook/telegram
  -> Secret Token + body limit + Redis rate limit
  -> PostgreSQL IncomingEvent Inbox
  -> Celery process_telegram_update_task
  -> explainable IntentResult + safety escalation gate
  -> RAG / GigaChat либо handoff
  -> Message + PostgreSQL Transactional Outbox
  -> Outbox notification Соне при эскалации
  -> Celery deliver_outbound_message_task
  -> Telegram Bot API
```

Terminal Inbox failure также не вызывает Telegram напрямую: fallback клиенту и
уведомление Соне атомарно сохраняются в Outbox. Celery Beat восстанавливает
готовые и зависшие Inbox/Outbox записи.

## Локальный baseline патча

Проверено в рабочем snapshot:

- Python compileall: PASS;
- Ruff lint: PASS;
- Ruff format: PASS;
- Alembic graph: одна base `2b5c075445e1`, один head `i9d0e1f2a3b4`;
- intent regression dataset: **169 фраз**;
- unit tests: **379 PASS**;
- statement coverage: **74.54%**;
- coverage gate: **74%**.

PostgreSQL integration collection содержит 10 тестов. Полный запуск с реальной
БД и Docker image выполняется GitHub Actions и локально командой
`scripts/run_postgres_integration.py`; в среде подготовки патча Docker daemon
недоступен.

## Канонические команды

```bash
uv sync --locked --extra dev
uv run --no-sync ruff check src tests scripts migrations
uv run --no-sync ruff format --check src tests scripts migrations
uv run --no-sync python scripts/check_migration_graph.py
uv run --no-sync python -m pytest tests/unit -v \
  --cov=src/app --cov-fail-under=74
uv run --no-sync python scripts/run_postgres_integration.py
```

Строгая типизация пока является отдельным диагностическим этапом:

```bash
python scripts/validate_repository.py --type-check
```

Её нельзя объявлять зелёной до планового устранения type debt; массовые
`ignore` не считаются исправлением.

## Новые safety-инварианты

1. Классификация возвращает intent, confidence и matched rules.
2. Критичные сценарии проходят детерминированный safety-gate до вызова LLM.
3. Prompt injection не передаётся внешнему AI как обычный клиентский запрос.
4. Ответ клиенту и уведомление Соне сначала фиксируются в PostgreSQL Outbox.
5. Уведомления идемпотентны по causation event и восстанавливаются Celery Beat.
6. Terminal fallback не обходит Outbox и не зависит от доступности broker в
   момент фиксации бизнес-эффекта.
7. Новая бизнес-функция сопровождается unit или PostgreSQL integration-тестом.
8. В проекте должен оставаться ровно один Alembic head.
