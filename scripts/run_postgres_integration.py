"""Create a clean local PostgreSQL test database and run integration tests.

The command is intentionally cross-platform and avoids shell-specific quoting.
It uses the PostgreSQL container from the development Compose configuration,
applies all Alembic migrations to a dedicated database, and then runs the
integration suite against that database.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from app.core.config import Settings  # noqa: E402

COMPOSE_FILES = (
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.dev.yml",
)
SAFE_DATABASE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _run(command: Sequence[str], *, env: dict[str, str] | None = None) -> None:
    print(f"\n$ {' '.join(command)}", flush=True)
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )


def _docker_compose(*args: str) -> tuple[str, ...]:
    return ("docker", "compose", *COMPOSE_FILES, *args)


def _database_dsn(settings: Settings, database_name: str) -> str:
    prefix, _separator, _current_database = settings.postgres_dsn.rpartition("/")
    if not prefix:
        raise RuntimeError("Cannot derive TEST_POSTGRES_DSN from PostgreSQL settings")
    return f"{prefix}/{database_name}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default="tattoo_assistant_test",
        help="Dedicated database that may be dropped and recreated.",
    )
    parser.add_argument(
        "--skip-compose-up",
        action="store_true",
        help="Do not start PostgreSQL and Redis before validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not SAFE_DATABASE_NAME.fullmatch(args.database):
        raise SystemExit(
            "Unsafe database name. Use only letters, digits and underscores, "
            "starting with a letter or underscore."
        )

    settings = Settings()
    if not args.skip_compose_up:
        _run(_docker_compose("up", "-d", "postgres", "redis"))

    _run(
        _docker_compose(
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            "-U",
            settings.postgres_user,
            "-d",
            settings.postgres_db,
        )
    )

    drop_sql = f"DROP DATABASE IF EXISTS {args.database} WITH (FORCE);"
    create_sql = f"CREATE DATABASE {args.database};"
    for sql in (drop_sql, create_sql):
        _run(
            _docker_compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                settings.postgres_user,
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                sql,
            )
        )

    test_env = os.environ.copy()
    test_env["PYTHONPATH"] = "src"
    test_env["POSTGRES_DB"] = args.database
    test_env["TEST_POSTGRES_DSN"] = _database_dsn(settings, args.database)

    _run((sys.executable, "-m", "alembic", "upgrade", "head"), env=test_env)
    _run((sys.executable, "-m", "alembic", "current", "--check-heads"), env=test_env)
    _run(
        (
            sys.executable,
            "-m",
            "pytest",
            "tests/integration",
            "-v",
            "--no-cov",
        ),
        env=test_env,
    )

    print("\nPostgreSQL integration validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
