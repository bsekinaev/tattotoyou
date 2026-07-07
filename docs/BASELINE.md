# Repository validation baseline

Date: 2026-07-07  
Snapshot: `repomix-output.xml`

Этот документ фиксирует проверяемую инженерную базу проекта. Он не является
утверждением о production-ready статусе: интеграционный и container-контуры
считаются подтверждёнными только после зелёного запуска GitHub Actions.

## Активный execution path

```text
POST /webhook/telegram
  -> Secret Token + body limit + Redis rate limit
  -> PostgreSQL IncomingEvent Inbox
  -> Celery process_telegram_update_task
  -> ConversationService / RAG / GigaChat / escalation
  -> Message + PostgreSQL Transactional Outbox
  -> Celery deliver_outbound_message_task
  -> Telegram Bot API
```

Celery Beat восстанавливает готовые и зависшие Inbox/Outbox записи. Ручной
ответ Сони также сохраняется через Outbox. Панель управляет handoff-состояниями,
заявками, предоплатой и расписанием.

## Локальный baseline

Проверено на извлечённом snapshot:

- Ruff lint: PASS;
- Ruff format: PASS;
- Alembic graph: одна base `2b5c075445e1`, один head `h8c9d0e1f2a3`;
- unit tests: **181 PASS**;
- statement coverage: **73.40%**;
- coverage gate: **73%**.

Полный PostgreSQL integration и Docker build не исполнялись в текущем
контейнерном окружении из-за отсутствия Docker daemon. Их выполняет обновлённый
GitHub Actions workflow.

## Канонические команды

```bash
uv sync --locked --extra dev
uv run --locked --extra dev ruff check src tests scripts migrations
uv run --locked --extra dev ruff format --check src tests scripts migrations
uv run --locked --extra dev python scripts/check_migration_graph.py
uv run --locked --extra dev python -m pytest tests/unit -v \
  --cov=src/app --cov-fail-under=73
```

PostgreSQL integration:

```bash
uv run --locked --extra dev alembic upgrade head
uv run --locked --extra dev alembic current --check-heads
uv run --locked --extra dev python -m pytest tests/integration -v --no-cov
```

Строгая типизация пока является отдельным диагностическим этапом:

```bash
python scripts/validate_repository.py --type-check
```

Её нельзя объявлять зелёной до планового устранения type debt; массовые
`ignore` не считаются исправлением.

## Инварианты развития

1. Новая бизнес-функция сопровождается unit или integration-тестом.
2. Миграции всегда проверяются на пустой PostgreSQL с pgvector.
3. В проекте должен оставаться ровно один Alembic head.
4. Внешний ответ сначала фиксируется в БД, затем отправляется платформе.
5. Чувствительные сценарии используют handoff, а не неподтверждённую генерацию.
6. README и roadmap отражают только исполняемое и проверяемое поведение.
