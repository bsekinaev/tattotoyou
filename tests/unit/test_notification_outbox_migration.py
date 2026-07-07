"""Проверка миграции универсального Outbox для уведомлений."""

from pathlib import Path


def test_notification_outbox_migration_extends_booking_head() -> None:
    migration = Path("migrations/versions/i9d0e1f2a3b4_generalize_outbound_delivery.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "i9d0e1f2a3b4"' in migration
    assert 'down_revision: str | None = "h8c9d0e1f2a3"' in migration
    assert '"delivery_kind"' in migration
    assert '"payload_text"' in migration
    assert '"deduplication_key"' in migration
    assert "ck_outbound_deliveries_payload_source" in migration
    assert "uq_outbound_deliveries_deduplication_key" in migration
