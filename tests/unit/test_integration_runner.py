from types import SimpleNamespace

from scripts import run_postgres_integration as runner


def test_database_dsn_replaces_only_database_name() -> None:
    settings = SimpleNamespace(
        postgres_dsn="postgresql+psycopg://user:password@127.0.0.1:5434/main_db"
    )

    assert (
        runner._database_dsn(settings, "tattoo_assistant_test")
        == "postgresql+psycopg://user:password@127.0.0.1:5434/tattoo_assistant_test"
    )


def test_database_name_validation_rejects_shell_and_sql_payloads() -> None:
    assert runner.SAFE_DATABASE_NAME.fullmatch("tattoo_assistant_test")
    assert not runner.SAFE_DATABASE_NAME.fullmatch("test-db")
    assert not runner.SAFE_DATABASE_NAME.fullmatch("test; DROP DATABASE postgres")


def test_docker_compose_command_uses_both_configuration_files() -> None:
    command = runner._docker_compose("up", "-d", "postgres", "redis")

    assert command == (
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.dev.yml",
        "up",
        "-d",
        "postgres",
        "redis",
    )
