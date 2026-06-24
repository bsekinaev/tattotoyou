"""Regression tests for the pgvector schema migration."""

from importlib import import_module
from unittest.mock import Mock


def test_vector_extension_is_enabled_before_column_creation(monkeypatch) -> None:
    migration = import_module("migrations.versions.c3d4e5f6a7b8_add_question_vector")
    calls: list[tuple[str, object]] = []

    execute = Mock(side_effect=lambda sql: calls.append(("execute", sql)))
    add_column = Mock(
        side_effect=lambda table, column: calls.append(("add_column", (table, column.name)))
    )
    create_index = Mock()

    monkeypatch.setattr(migration.op, "execute", execute)
    monkeypatch.setattr(migration.op, "add_column", add_column)
    monkeypatch.setattr(migration.op, "create_index", create_index)

    migration.upgrade()

    assert calls == [
        ("execute", "CREATE EXTENSION IF NOT EXISTS vector"),
        ("add_column", ("knowledge_base", "question_vector")),
    ]
    create_index.assert_not_called()
