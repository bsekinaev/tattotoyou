"""add knowledge_base

Revision ID: a1b2c3d4e5f6
Revises: f06ddef3835f
Create Date: 2026-06-22 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f06ddef3835f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "knowledge_base",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "category",
            sa.String(length=50),
            nullable=False,
            comment="pricing, aftercare, styles, faq, contraindications, booking",
        ),
        sa.Column("question", sa.Text(), nullable=False, comment="Вопрос клиента"),
        sa.Column("answer", sa.Text(), nullable=False, comment="Эталонный ответ"),
        sa.Column(
            "keywords",
            sa.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
            comment="Ключевые слова для быстрого поиска",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="Активен ли FAQ (можно скрыть без удаления)",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Приоритет (выше = важнее)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Индексы для производительности
    op.create_index("ix_kb_category", "knowledge_base", ["category"], unique=False)
    op.create_index(
        "ix_kb_active_priority", "knowledge_base", ["is_active", "priority"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_kb_active_priority", table_name="knowledge_base")
    op.drop_index("ix_kb_category", table_name="knowledge_base")
    op.drop_table("knowledge_base")