"""Validate that the Alembic revision graph has one base and one head."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CONFIG = PROJECT_ROOT / "alembic.ini"


def inspect_revision_graph() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return Alembic bases and heads without connecting to PostgreSQL."""
    config = Config(str(ALEMBIC_CONFIG))
    script = ScriptDirectory.from_config(config)
    return tuple(script.get_bases()), tuple(script.get_heads())


def main() -> int:
    """Fail when migration history is missing, branched, or has multiple heads."""
    bases, heads = inspect_revision_graph()

    if len(bases) != 1:
        print(f"Alembic revision graph: INVALID — expected 1 base, found {len(bases)}: {bases}")
        return 1
    if len(heads) != 1:
        print(f"Alembic revision graph: INVALID — expected 1 head, found {len(heads)}: {heads}")
        return 1

    print(f"Alembic revision graph: OK — base={bases[0]} head={heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
