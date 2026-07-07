"""Контракт миграции заявок и расписания."""

from pathlib import Path


def test_booking_migration_extends_dashboard_head_and_guards_overlap() -> None:
    migration = Path(
        "migrations/versions/h8c9d0e1f2a3_add_tattoo_applications_and_appointments.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "h8c9d0e1f2a3"' in migration
    assert 'down_revision: str | Sequence[str] | None = "g7b8c9d0e1f2"' in migration
    assert '"tattoo_applications"' in migration
    assert '"application_status_history"' in migration
    assert '"application_references"' in migration
    assert '"appointments"' in migration
    assert "excl_appointments_no_active_overlap" in migration
    assert "tstzrange(scheduled_start, scheduled_end, '[)') WITH &&" in migration
    assert "uq_tattoo_applications_one_open_per_conversation" in migration


def test_booking_migration_drops_exclusion_constraint_with_postgresql_sql() -> None:
    migration = Path(
        "migrations/versions/h8c9d0e1f2a3_add_tattoo_applications_and_appointments.py"
    ).read_text(encoding="utf-8")

    assert "DROP CONSTRAINT excl_appointments_no_active_overlap" in migration
    assert 'type_="exclude"' not in migration
