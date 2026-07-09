"""Контракт веток production-oriented CI."""

from pathlib import Path


def test_ci_runs_on_actual_development_branch() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "branches: [main, dev]" in workflow
    assert "branches: [main, develop]" not in workflow
