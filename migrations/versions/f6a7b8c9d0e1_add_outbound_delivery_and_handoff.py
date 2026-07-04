"""add outbound delivery outbox and conversation handoff

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать Transactional Outbox и закрепить state machine диалогов."""
    op.execute(
        """
        UPDATE conversations
        SET status = 'closed',
            assigned_to_human = false,
            closed_at = COALESCE(closed_at, now())
        WHERE status NOT IN ('active', 'escalated', 'human_owned', 'closed', 'spam')
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY client_id
                       ORDER BY last_activity_at DESC, created_at DESC, id DESC
                   ) AS row_number
            FROM conversations
            WHERE status IN ('active', 'escalated', 'human_owned')
        )
        UPDATE conversations AS conversation
        SET status = 'closed',
            assigned_to_human = false,
            closed_at = COALESCE(conversation.closed_at, now())
        FROM ranked
        WHERE conversation.id = ranked.id
          AND ranked.row_number > 1
        """
    )

    op.drop_index(
        "uq_conversations_one_active_per_client",
        table_name="conversations",
    )
    op.create_check_constraint(
        "ck_conversations_status",
        "conversations",
        "status IN ('active', 'escalated', 'human_owned', 'closed', 'spam')",
    )
    op.create_index(
        "uq_conversations_one_active_per_client",
        "conversations",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('active', 'escalated', 'human_owned')"),
    )

    op.add_column(
        "messages",
        sa.Column(
            "sender_type",
            sa.String(length=20),
            server_default=sa.text("'bot'"),
            nullable=True,
            comment="client, bot, human, system",
        ),
    )
    op.execute("UPDATE messages SET sender_type = 'client' WHERE direction = 'inbound'")
    op.execute("UPDATE messages SET sender_type = 'bot' WHERE direction = 'outbound'")
    op.alter_column("messages", "sender_type", nullable=False)
    op.create_check_constraint(
        "ck_messages_sender_type",
        "messages",
        "sender_type IN ('client', 'bot', 'human', 'system')",
    )

    op.create_table(
        "outbound_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("destination_id", sa.String(length=128), nullable=False),
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
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("platform_message_id", sa.String(length=128), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'sending', 'retrying', 'sent', 'failed')",
            name="ck_outbound_deliveries_status",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            name="uq_outbound_deliveries_message_id",
        ),
    )
    op.create_index(
        "ix_outbound_deliveries_dispatch",
        "outbound_deliveries",
        ["status", "next_attempt_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_outbound_deliveries_processing_started_at",
        "outbound_deliveries",
        ["processing_started_at"],
        unique=False,
    )


def downgrade() -> None:
    """Удалить Outbox и вернуть прежнюю модель активного диалога."""
    op.drop_index(
        "ix_outbound_deliveries_processing_started_at",
        table_name="outbound_deliveries",
    )
    op.drop_index(
        "ix_outbound_deliveries_dispatch",
        table_name="outbound_deliveries",
    )
    op.drop_table("outbound_deliveries")

    op.drop_constraint("ck_messages_sender_type", "messages", type_="check")
    op.drop_column("messages", "sender_type")

    op.drop_index(
        "uq_conversations_one_active_per_client",
        table_name="conversations",
    )
    op.drop_constraint("ck_conversations_status", "conversations", type_="check")
    op.execute(
        """
        UPDATE conversations
        SET status = 'escalated', assigned_to_human = true
        WHERE status = 'human_owned'
        """
    )
    op.create_index(
        "uq_conversations_one_active_per_client",
        "conversations",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
