"""enforce booking financial invariants

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "j0e1f2a3b4c5"
down_revision: str | None = "i9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Запретить обход state machine и неконсистентные денежные состояния."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM appointments
                WHERE
                    (deposit_status = 'not_required' AND COALESCE(deposit_amount, 0) <> 0)
                    OR (
                        deposit_status IN ('pending', 'paid', 'refunded')
                        AND COALESCE(deposit_amount, 0) <= 0
                    )
            ) THEN
                RAISE EXCEPTION
                    'appointments contain deposit status/amount inconsistencies; repair data before migration';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM appointments
                WHERE
                    quoted_price IS NOT NULL
                    AND deposit_amount IS NOT NULL
                    AND deposit_amount > quoted_price
            ) THEN
                RAISE EXCEPTION
                    'appointments contain deposits above quoted price; repair data before migration';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM appointments
                WHERE deposit_status = 'refunded' AND status <> 'canceled'
            ) THEN
                RAISE EXCEPTION
                    'refunded deposits require canceled appointments; repair data before migration';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM appointments
                WHERE
                    status IN ('confirmed', 'completed')
                    AND deposit_status NOT IN ('paid', 'not_required')
            ) THEN
                RAISE EXCEPTION
                    'confirmed/completed appointments require paid or waived deposits';
            END IF;
        END
        $$
        """
    )
    op.create_check_constraint(
        "ck_appointments_deposit_amount_by_status",
        "appointments",
        "(deposit_status = 'not_required' AND "
        "(deposit_amount IS NULL OR deposit_amount = 0)) OR "
        "(deposit_status IN ('pending', 'paid', 'refunded') AND deposit_amount IS NOT NULL AND deposit_amount > 0)",
    )
    op.create_check_constraint(
        "ck_appointments_deposit_not_above_price",
        "appointments",
        "quoted_price IS NULL OR deposit_amount IS NULL OR deposit_amount <= quoted_price",
    )
    op.create_check_constraint(
        "ck_appointments_refund_requires_cancellation",
        "appointments",
        "deposit_status != 'refunded' OR status = 'canceled'",
    )
    op.create_check_constraint(
        "ck_appointments_confirmation_financial_state",
        "appointments",
        "status NOT IN ('confirmed', 'completed') OR deposit_status IN ('paid', 'not_required')",
    )
    op.create_index(
        "ix_appointments_refund_due",
        "appointments",
        ["canceled_at"],
        unique=False,
        postgresql_where=sa.text("status = 'canceled' AND deposit_status = 'paid'"),
    )


def downgrade() -> None:
    """Удалить финансовые ограничения и индекс очереди возвратов."""
    op.drop_index("ix_appointments_refund_due", table_name="appointments")
    op.drop_constraint(
        "ck_appointments_confirmation_financial_state",
        "appointments",
        type_="check",
    )
    op.drop_constraint(
        "ck_appointments_refund_requires_cancellation",
        "appointments",
        type_="check",
    )
    op.drop_constraint(
        "ck_appointments_deposit_not_above_price",
        "appointments",
        type_="check",
    )
    op.drop_constraint(
        "ck_appointments_deposit_amount_by_status",
        "appointments",
        type_="check",
    )
