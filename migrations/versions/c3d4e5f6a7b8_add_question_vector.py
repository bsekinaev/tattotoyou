"""add question_vector to knowledge_base

Revision ID: c3d4e5f6a7b8
Revises: f06ddef3835f
Create Date: 2026-06-22 18:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'  # ← ОБЯЗАТЕЛЬНО В КАВЫЧКАХ!
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Размерность 384 — для paraphrase-multilingual-MiniLM-L12-v2
    op.add_column(
        'knowledge_base',
        sa.Column('question_vector', Vector(384), nullable=True)
    )
    # IVFFlat индекс для быстрого cosine similarity search
    op.create_index(
        'ix_kb_question_vector',
        'knowledge_base',
        ['question_vector'],
        postgresql_using='ivfflat',
        postgresql_with={'lists': 10},
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_kb_question_vector', table_name='knowledge_base')
    op.drop_column('knowledge_base', 'question_vector')