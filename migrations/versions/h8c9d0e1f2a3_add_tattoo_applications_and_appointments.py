"""add tattoo applications and appointments

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-07-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "g7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Создать отдельные заявки, референсы, историю и записи на сеанс."""
    op.create_table(
        "tattoo_applications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="new", nullable=False),
        sa.Column("idea", sa.Text(), nullable=True),
        sa.Column("placement", sa.String(length=120), nullable=True),
        sa.Column("size_details", sa.String(length=120), nullable=True),
        sa.Column(
            "color_mode",
            sa.String(length=20),
            server_default="undecided",
            nullable=False,
        ),
        sa.Column("style_preferences", sa.String(length=200), nullable=True),
        sa.Column("budget_min", sa.Integer(), nullable=True),
        sa.Column("budget_max", sa.Integer(), nullable=True),
        sa.Column("desired_date", sa.Date(), nullable=True),
        sa.Column("client_comment", sa.Text(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('new', 'qualification', 'consultation', 'awaiting_deposit', "
            "'booked', 'completed', 'canceled', 'rejected')",
            name="ck_tattoo_applications_status",
        ),
        sa.CheckConstraint(
            "color_mode IN ('black_and_grey', 'color', 'undecided')",
            name="ck_tattoo_applications_color_mode",
        ),
        sa.CheckConstraint(
            "budget_min IS NULL OR budget_min >= 0",
            name="ck_tattoo_applications_budget_min_nonnegative",
        ),
        sa.CheckConstraint(
            "budget_max IS NULL OR budget_max >= 0",
            name="ck_tattoo_applications_budget_max_nonnegative",
        ),
        sa.CheckConstraint(
            "budget_min IS NULL OR budget_max IS NULL OR budget_max >= budget_min",
            name="ck_tattoo_applications_budget_range",
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tattoo_applications_client_id",
        "tattoo_applications",
        ["client_id"],
        unique=False,
    )
    op.create_index(
        "ix_tattoo_applications_conversation_id",
        "tattoo_applications",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_tattoo_applications_client_status",
        "tattoo_applications",
        ["client_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_tattoo_applications_desired_date",
        "tattoo_applications",
        ["desired_date"],
        unique=False,
    )
    op.create_index(
        "uq_tattoo_applications_one_open_per_conversation",
        "tattoo_applications",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(
            "conversation_id IS NOT NULL AND status IN "
            "('new', 'qualification', 'consultation', 'awaiting_deposit', 'booked')"
        ),
    )

    op.create_table(
        "application_references",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("reference_type", sa.String(length=30), nullable=False),
        sa.Column("value", sa.String(length=2048), nullable=False),
        sa.Column("file_unique_id", sa.String(length=255), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("source_message_id", sa.String(length=100), nullable=True),
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
            "reference_type IN ('telegram_file', 'url')",
            name="ck_application_references_type",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["tattoo_applications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_references_application",
        "application_references",
        ["application_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "application_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("from_status", sa.String(length=30), nullable=True),
        sa.Column("to_status", sa.String(length=30), nullable=False),
        sa.Column("actor", sa.String(length=20), server_default="studio", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
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
            "to_status IN ('new', 'qualification', 'consultation', 'awaiting_deposit', "
            "'booked', 'completed', 'canceled', 'rejected')",
            name="ck_application_status_history_to_status",
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('new', 'qualification', 'consultation', 'awaiting_deposit', "
            "'booked', 'completed', 'canceled', 'rejected')",
            name="ck_application_status_history_from_status",
        ),
        sa.CheckConstraint(
            "actor IN ('studio', 'system')",
            name="ck_application_status_history_actor",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["tattoo_applications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_status_history_application",
        "application_status_history",
        ["application_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("quoted_price", sa.Integer(), nullable=True),
        sa.Column("deposit_amount", sa.Integer(), nullable=True),
        sa.Column(
            "deposit_status",
            sa.String(length=20),
            server_default="not_required",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("canceled_reason", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('draft', 'pending', 'confirmed', 'completed', 'canceled')",
            name="ck_appointments_status",
        ),
        sa.CheckConstraint(
            "deposit_status IN ('not_required', 'pending', 'paid', 'refunded')",
            name="ck_appointments_deposit_status",
        ),
        sa.CheckConstraint(
            "scheduled_end > scheduled_start",
            name="ck_appointments_positive_period",
        ),
        sa.CheckConstraint(
            "duration_minutes > 0 AND duration_minutes <= 1440",
            name="ck_appointments_duration",
        ),
        sa.CheckConstraint(
            "quoted_price IS NULL OR quoted_price >= 0",
            name="ck_appointments_quoted_price_nonnegative",
        ),
        sa.CheckConstraint(
            "deposit_amount IS NULL OR deposit_amount >= 0",
            name="ck_appointments_deposit_amount_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["tattoo_applications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", name="uq_appointments_application_id"),
    )
    op.create_index(
        "ix_appointments_status_start",
        "appointments",
        ["status", "scheduled_start"],
        unique=False,
    )
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT excl_appointments_no_active_overlap
        EXCLUDE USING gist (
            tstzrange(scheduled_start, scheduled_end, '[)') WITH &&
        )
        WHERE (status IN ('pending', 'confirmed'))
        """
    )


def downgrade() -> None:
    """Удалить продуктовый контур заявок и записей."""
    op.execute("ALTER TABLE appointments DROP CONSTRAINT excl_appointments_no_active_overlap")
    op.drop_index("ix_appointments_status_start", table_name="appointments")
    op.drop_table("appointments")

    op.drop_index(
        "ix_application_status_history_application",
        table_name="application_status_history",
    )
    op.drop_table("application_status_history")

    op.drop_index(
        "ix_application_references_application",
        table_name="application_references",
    )
    op.drop_table("application_references")

    op.drop_index(
        "uq_tattoo_applications_one_open_per_conversation",
        table_name="tattoo_applications",
    )
    op.drop_index("ix_tattoo_applications_desired_date", table_name="tattoo_applications")
    op.drop_index("ix_tattoo_applications_client_status", table_name="tattoo_applications")
    op.drop_index("ix_tattoo_applications_conversation_id", table_name="tattoo_applications")
    op.drop_index("ix_tattoo_applications_client_id", table_name="tattoo_applications")
    op.drop_table("tattoo_applications")
