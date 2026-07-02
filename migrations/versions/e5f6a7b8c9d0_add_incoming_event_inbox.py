"""add durable incoming event inbox

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-02 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать Inbox и связать сообщения с породившим событием."""
    op.create_table(
        "incoming_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("external_event_id", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retrying', 'processed', 'failed')",
            name="ck_incoming_events_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform",
            "external_event_id",
            name="uq_incoming_events_platform_external",
        ),
    )
    op.create_index(
        "ix_incoming_events_dispatch",
        "incoming_events",
        ["status", "next_attempt_at", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_incoming_events_processing_started_at",
        "incoming_events",
        ["processing_started_at"],
        unique=False,
    )

    op.add_column(
        "messages",
        sa.Column("causation_event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_causation_event_id_incoming_events",
        "messages",
        "incoming_events",
        ["causation_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_messages_causation_event_id",
        "messages",
        ["causation_event_id"],
        unique=False,
    )
    op.create_index(
        "uq_messages_causation_direction",
        "messages",
        ["causation_event_id", "direction"],
        unique=True,
        postgresql_where=sa.text("causation_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Удалить Inbox и идемпотентные связи сообщений."""
    op.drop_index(
        "uq_messages_causation_direction",
        table_name="messages",
    )
    op.drop_index(
        "ix_messages_causation_event_id",
        table_name="messages",
    )
    op.drop_constraint(
        "fk_messages_causation_event_id_incoming_events",
        "messages",
        type_="foreignkey",
    )
    op.drop_column("messages", "causation_event_id")

    op.drop_index(
        "ix_incoming_events_processing_started_at",
        table_name="incoming_events",
    )
    op.drop_index(
        "ix_incoming_events_dispatch",
        table_name="incoming_events",
    )
    op.drop_table("incoming_events")
