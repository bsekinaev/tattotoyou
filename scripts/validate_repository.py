"""Run repository validation checks in a deterministic order.

The script intentionally performs no source-code modifications. It is the local
counterpart of CI and helps distinguish build, static-analysis, migration, and
test failures before a change is committed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = ("src", "tests", "scripts", "migrations")


@dataclass(frozen=True, slots=True)
class Check:
    """Single repository validation command."""

    name: str
    command: tuple[str, ...]
    required_executable: str | None = None


def _project_tool_command(*args: str) -> tuple[str, ...]:
    """Build a deterministic command for an installed project tool.

    A frozen uv environment is used only when ``uv.lock`` exists. Without a
    lock file, ``uv run`` may resolve dependencies or download Python during a
    validation run, turning a lint check into a network-dependent operation.
    """
    if (PROJECT_ROOT / "uv.lock").exists() and shutil.which("uv"):
        return ("uv", "run", "--frozen", "--extra", "dev", *args)
    return args


def build_checks() -> tuple[Check, ...]:
    """Return all repository checks in fail-fast diagnostic order."""
    return (
        Check(
            name="Python syntax",
            command=(sys.executable, "-m", "compileall", "-q", *SOURCE_PATHS),
        ),
        Check(
            name="Ruff lint",
            command=_project_tool_command("ruff", "check", *SOURCE_PATHS),
            required_executable="uv" if (PROJECT_ROOT / "uv.lock").exists() else "ruff",
        ),
        Check(
            name="Ruff formatting",
            command=_project_tool_command("ruff", "format", "--check", *SOURCE_PATHS),
            required_executable="uv" if (PROJECT_ROOT / "uv.lock").exists() else "ruff",
        ),
        Check(
            name="Mypy",
            command=_project_tool_command("mypy", "src"),
            required_executable="uv" if (PROJECT_ROOT / "uv.lock").exists() else "mypy",
        ),
        Check(
            name="Unit tests",
            command=_project_tool_command("pytest", "tests/unit", "-q"),
            required_executable="uv" if (PROJECT_ROOT / "uv.lock").exists() else "pytest",
        ),
        Check(
            name="Alembic revision graph",
            command=_project_tool_command("alembic", "heads"),
            required_executable="uv" if (PROJECT_ROOT / "uv.lock").exists() else "alembic",
        ),
        Check(
            name="Docker Compose production configuration",
            command=("docker", "compose", "-f", "docker-compose.yml", "config", "--quiet"),
            required_executable="docker",
        ),
        Check(
            name="Docker Compose development configuration",
            command=(
                "docker",
                "compose",
                "-f",
                "docker-compose.yml",
                "-f",
                "docker-compose.dev.yml",
                "config",
                "--quiet",
            ),
            required_executable="docker",
        ),
    )


def run_check(check: Check, env: dict[str, str]) -> int | None:
    """Run one check and return its exit code, or None when tooling is absent."""
    if check.required_executable and not shutil.which(check.required_executable):
        print(f"SKIP  {check.name}: missing executable {check.required_executable}")
        return None

    command_text = " ".join(check.command)
    print(f"\n=== {check.name} ===")
    print(f"$ {command_text}")

    completed = subprocess.run(
        check.command,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )
    status = "PASS" if completed.returncode == 0 else "FAIL"
    print(f"{status}  {check.name} (exit={completed.returncode})")
    return completed.returncode


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only syntax, Ruff lint, and Ruff formatting checks.",
    )
    return parser.parse_args()


def main() -> int:
    """Execute validation and return a non-zero status when a check fails."""
    args = parse_args()
    checks: Sequence[Check] = build_checks()
    if args.quick:
        checks = checks[:3]

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", "src")
    env.setdefault("TELEGRAM_BOT_TOKEN", "validation_token")
    env.setdefault("TELEGRAM_WEBHOOK_SECRET", "validation_secret")
    env.setdefault("TELEGRAM_ADMIN_CHAT_ID", "123456")
    env.setdefault("GIGACHAT_CLIENT_ID", "validation_client")
    env.setdefault("GIGACHAT_CLIENT_SECRET", "validation_secret")
    env.setdefault("POSTGRES_PASSWORD", "validation_password")
    env.setdefault("REDIS_PASSWORD", "validation_redis_password")
    env.setdefault("SECRET_KEY", "validation_secret_key_at_least_32_chars_long")
    env.setdefault("ADMIN_API_KEY", "validation_admin_key_at_least_32_chars_long")

    failed = 0
    skipped = 0
    for check in checks:
        result = run_check(check, env)
        if result is None:
            skipped += 1
        elif result != 0:
            failed += 1

    print("\n=== Summary ===")
    print(f"checks={len(checks)} failed={failed} skipped={skipped}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())