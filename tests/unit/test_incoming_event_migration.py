"""Проверка миграции долговечного Inbox."""

from pathlib import Path


def test_incoming_event_migration_extends_current_head() -> None:
    migration = Path("migrations/versions/e5f6a7b8c9d0_add_incoming_event_inbox.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "e5f6a7b8c9d0"' in migration
    assert 'down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"' in migration
    assert '"incoming_events"' in migration
    assert '"uq_incoming_events_platform_external"' in migration
    assert '"uq_messages_causation_direction"' in migration
    assert 'ondelete="RESTRICT"' in migration
