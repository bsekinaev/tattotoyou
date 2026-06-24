# Repository validation baseline

Date: 2026-06-23  
Snapshot: `repomix-output(14).xml`

This document records the state observed before stabilization changes. It is not
a production-readiness claim. A check is considered fixed only after its command
passes in CI and locally on a clean environment.

## Active execution path

The current Telegram request path is:

```text
POST /webhook/telegram
  -> TelegramUpdate validation
  -> secret-token comparison
  -> Redis rate limit and SETNX deduplication
  -> process_telegram_update_task
  -> TelegramAdapter.parse_message
  -> ConversationService.process_message
  -> repositories / GigaChat / escalation
```

`src/app/services/platforms/telegram/service.py` defines the older
`TelegramMessageService`. Repository-wide reference search found no imports of
that class outside its own module, while the active Celery task uses
`ConversationService`.

## Validation commands

Run the complete local validation suite:

```bash
python scripts/validate_repository.py
```

Run only syntax and Ruff checks:

```bash
python scripts/validate_repository.py --quick
```

## Observed failures before stabilization

### Python syntax

```text
src/app/services/platforms/telegram/service.py:143
SyntaxError: invalid syntax
```

The invalid legacy module prevents `compileall`, Ruff formatting, and mypy from
analysing the complete codebase.

### Ruff

The initial run reported 61 findings, including:

- the syntax error in the legacy Telegram service;
- unsorted imports;
- missing final newlines;
- one unused import;
- an empty concrete method in an abstract base class;
- formatting drift across multiple files.

Formatting changes must be committed separately from business-logic changes.

### Unit tests

Running tests directly in the system Python environment failed during collection
because project dependencies such as `structlog` and `redis` were not installed.
The canonical command therefore runs tests through `uv` with the `dev` extra.

### Migrations and RAG dependencies

The pgvector migration imports `pgvector.sqlalchemy`, while `pgvector` is not
declared in `pyproject.toml`. The embedding service imports
`sentence_transformers`, which is also not declared. Migration and runtime RAG
validation cannot be considered reproducible until both dependencies are added
and locked.

### Docker

`docker-compose.yml` contains services with `build: .`, but no Dockerfile exists
in the snapshot. Docker build validation is expected to fail until the build
stage is implemented.

## Baseline invariants

During stabilization:

1. No business feature is marked complete unless it is used by the active path.
2. Syntax, lint, typing, migrations, tests, and container build are separate gates.
3. A formatting-only commit must not change behaviour.
4. Legacy code is removed only after repository-wide reference verification.
5. README statements must match executable code and automated tests.