"""Regression tests for Telegram webhook secret handling."""

import ast
from pathlib import Path

import pytest

from app.core.security import secrets_match


@pytest.mark.parametrize(
    ("provided", "expected", "is_valid"),
    [
        ("correct-secret", "correct-secret", True),
        ("wrong-secret", "correct-secret", False),
        (None, "correct-secret", False),
        ("", "correct-secret", False),
    ],
)
def test_secrets_match(provided: str | None, expected: str, is_valid: bool) -> None:
    assert secrets_match(provided, expected) is is_valid


def test_webhook_secret_is_not_part_of_worker_contract() -> None:
    """The verified ingress secret must not be serialized into the broker payload."""
    task_path = Path("src/app/workers/tasks/process_telegram_update.py")
    module = ast.parse(task_path.read_text(encoding="utf-8"))

    functions = {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    task_args = [arg.arg for arg in functions["process_telegram_update_task"].args.args]
    process_args = [arg.arg for arg in functions["_process_async"].args.args]

    assert "webhook_secret" not in task_args
    assert "webhook_secret" not in process_args