"""Проверка миграции продуктовой панели."""

from pathlib import Path


def test_studio_dashboard_migration_extends_rag_head() -> None:
    migration = Path("migrations/versions/g7b8c9d0e1f2_add_studio_dashboard_fields.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "g7b8c9d0e1f2"' in migration
    assert 'down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"' in migration
    assert '"lead_status"' in migration
    assert '"internal_notes"' in migration
    assert '"last_read_at"' in migration
