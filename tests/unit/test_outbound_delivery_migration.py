"""Проверка цепочки миграции Outbox и handoff."""

from pathlib import Path


def test_outbound_migration_extends_inbox_head() -> None:
    migration = Path(
        "migrations/versions/f6a7b8c9d0e1_add_outbound_delivery_and_handoff.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "f6a7b8c9d0e1"' in migration
    assert 'down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"' in migration
    assert '"outbound_deliveries"' in migration
    assert '"sender_type"' in migration
    assert "human_owned" in migration
