"""Repository-level checks for a linear Alembic migration history."""

from scripts.check_migration_graph import inspect_revision_graph


def test_alembic_revision_graph_has_single_base_and_head() -> None:
    bases, heads = inspect_revision_graph()

    assert len(bases) == 1
    assert len(heads) == 1
    assert heads[0] == "j0e1f2a3b4c5"
