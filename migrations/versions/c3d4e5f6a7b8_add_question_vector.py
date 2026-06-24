"""add question_vector to knowledge_base

Revision ID: c3d4e5f6a7b8
Revises: f06ddef3835f
Create Date: 2026-06-22 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable pgvector and add the nullable FAQ embedding column."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "knowledge_base",
        sa.Column("question_vector", Vector(384), nullable=True),
    )


def downgrade() -> None:
    """Remove the FAQ embedding column without dropping a shared extension."""
    op.drop_column("knowledge_base", "question_vector")
