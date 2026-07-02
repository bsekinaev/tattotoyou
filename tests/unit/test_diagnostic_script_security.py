"""Защита диагностических скриптов от утечки секретов и удаления БД."""

from pathlib import Path

SCRIPTS_DIR = Path("scripts")


def test_unsafe_legacy_diagnostic_scripts_are_removed() -> None:
    assert not (SCRIPTS_DIR / "test_config.py").exists()
    assert not (SCRIPTS_DIR / "test_dotenv.py").exists()
    assert not (SCRIPTS_DIR / "test_db.py").exists()


def test_scripts_do_not_contain_destructive_metadata_operations() -> None:
    sources = "\n".join(path.read_text(encoding="utf-8") for path in SCRIPTS_DIR.glob("*.py"))

    assert "drop_all" not in sources
    assert "Base.metadata.drop" not in sources


def test_configuration_check_never_prints_secret_values_or_full_dsns() -> None:
    source = (SCRIPTS_DIR / "check_configuration.py").read_text(encoding="utf-8")

    assert "settings.postgres_dsn" not in source
    assert "settings.redis_url" not in source
    assert "get_secret_value()" not in source
