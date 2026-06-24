"""enforce single active conversation per client

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-24 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Закрыть дубликаты и закрепить один active-диалог на клиента."""
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY client_id
                        ORDER BY last_activity_at DESC, created_at DESC, id DESC
                    ) AS row_number
                FROM conversations
                WHERE status = 'active'
            )
            UPDATE conversations AS conversation
            SET
                status = 'closed',
                closed_at = COALESCE(conversation.closed_at, now()),
                updated_at = now()
            FROM ranked
            WHERE conversation.id = ranked.id
              AND ranked.row_number > 1
            """
        )
    )
    op.create_index(
        "uq_conversations_one_active_per_client",
        "conversations",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    """Удалить частичный уникальный индекс."""
    op.drop_index(
        "uq_conversations_one_active_per_client",
        table_name="conversations",
    )
