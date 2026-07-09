"""Контракт миграции финансовых инвариантов записи."""

from pathlib import Path

MIGRATION = Path("migrations/versions/j0e1f2a3b4c5_enforce_booking_financial_invariants.py")


def test_financial_migration_extends_current_head() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "j0e1f2a3b4c5"' in migration
    assert 'down_revision: str | None = "i9d0e1f2a3b4"' in migration
    assert "ck_appointments_deposit_amount_by_status" in migration
    assert "deposit_amount IS NOT NULL AND deposit_amount > 0" in migration
    assert "ck_appointments_deposit_not_above_price" in migration
    assert "ck_appointments_refund_requires_cancellation" in migration
    assert "ck_appointments_confirmation_financial_state" in migration
    assert "ix_appointments_refund_due" in migration


def test_financial_migration_fails_before_accepting_legacy_invalid_data() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")

    assert "repair data before migration" in migration
    assert "deposit_amount > quoted_price" in migration
    assert "deposit_status = 'refunded' AND status <> 'canceled'" in migration
