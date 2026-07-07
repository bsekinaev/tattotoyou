"""generalize outbound delivery for durable notifications

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-07-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "i9d0e1f2a3b4"
down_revision: str | None = "h8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbound_deliveries",
        sa.Column(
            "delivery_kind",
            sa.String(length=32),
            server_default="message",
            nullable=False,
        ),
    )
    op.add_column(
        "outbound_deliveries",
        sa.Column("payload_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "outbound_deliveries",
        sa.Column("deduplication_key", sa.String(length=160), nullable=True),
    )
    op.alter_column("outbound_deliveries", "message_id", nullable=True)
    op.create_unique_constraint(
        "uq_outbound_deliveries_deduplication_key",
        "outbound_deliveries",
        ["deduplication_key"],
    )
    op.create_check_constraint(
        "ck_outbound_deliveries_kind",
        "outbound_deliveries",
        "delivery_kind IN ('message', 'notification')",
    )
    op.create_check_constraint(
        "ck_outbound_deliveries_payload_source",
        "outbound_deliveries",
        "(delivery_kind = 'message' AND message_id IS NOT NULL AND payload_text IS NULL) "
        "OR (delivery_kind = 'notification' AND message_id IS NULL "
        "AND payload_text IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_outbound_deliveries_payload_source",
        "outbound_deliveries",
        type_="check",
    )
    op.drop_constraint(
        "ck_outbound_deliveries_kind",
        "outbound_deliveries",
        type_="check",
    )
    op.drop_constraint(
        "uq_outbound_deliveries_deduplication_key",
        "outbound_deliveries",
        type_="unique",
    )
    # Старая схема не умеет хранить notification-delivery без Message.
    # Удаляем только такие системные записи перед восстановлением NOT NULL.
    op.execute("DELETE FROM outbound_deliveries WHERE message_id IS NULL")
    op.alter_column("outbound_deliveries", "message_id", nullable=False)
    op.drop_column("outbound_deliveries", "deduplication_key")
    op.drop_column("outbound_deliveries", "payload_text")
    op.drop_column("outbound_deliveries", "delivery_kind")
