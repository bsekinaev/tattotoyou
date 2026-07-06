"""add studio dashboard product fields

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавить продуктовый профиль клиента и отметку прочтения диалога."""
    op.add_column(
        "clients",
        sa.Column(
            "lead_status",
            sa.String(length=30),
            server_default=sa.text("'new'"),
            nullable=False,
            comment="Этап клиента в продуктовой воронке",
        ),
    )
    op.add_column("clients", sa.Column("internal_notes", sa.Text(), nullable=True))
    op.add_column("clients", sa.Column("tattoo_idea", sa.Text(), nullable=True))
    op.add_column("clients", sa.Column("placement", sa.String(length=120), nullable=True))
    op.add_column("clients", sa.Column("size_details", sa.String(length=120), nullable=True))
    op.add_column(
        "clients",
        sa.Column("style_preferences", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "clients",
        sa.Column("budget_details", sa.String(length=120), nullable=True),
    )
    op.add_column("clients", sa.Column("desired_date", sa.Date(), nullable=True))
    op.create_check_constraint(
        "ck_clients_lead_status",
        "clients",
        "lead_status IN ('new', 'qualification', 'consultation', "
        "'waiting_payment', 'booked', 'completed', 'lost')",
    )
    op.create_index("ix_clients_lead_status", "clients", ["lead_status"], unique=False)

    op.add_column(
        "conversations",
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Когда Соня последний раз открывала диалог в панели",
        ),
    )
    op.create_index(
        "ix_conversations_last_read_at",
        "conversations",
        ["last_read_at"],
        unique=False,
    )


def downgrade() -> None:
    """Удалить поля продуктовой панели."""
    op.drop_index("ix_conversations_last_read_at", table_name="conversations")
    op.drop_column("conversations", "last_read_at")

    op.drop_index("ix_clients_lead_status", table_name="clients")
    op.drop_constraint("ck_clients_lead_status", "clients", type_="check")
    op.drop_column("clients", "desired_date")
    op.drop_column("clients", "budget_details")
    op.drop_column("clients", "style_preferences")
    op.drop_column("clients", "size_details")
    op.drop_column("clients", "placement")
    op.drop_column("clients", "tattoo_idea")
    op.drop_column("clients", "internal_notes")
    op.drop_column("clients", "lead_status")
