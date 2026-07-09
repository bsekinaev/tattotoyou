"""Тесты продуктовой state machine заявок и записей."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.bookings.models import (
    APPLICATION_AWAITING_DEPOSIT,
    APPLICATION_BOOKED,
    APPLICATION_CONSULTATION,
    APPLICATION_NEW,
    APPLICATION_QUALIFICATION,
    APPOINTMENT_CANCELED,
    APPOINTMENT_CONFIRMED,
    APPOINTMENT_DRAFT,
    APPOINTMENT_PENDING,
    DEPOSIT_NOT_REQUIRED,
    DEPOSIT_PAID,
    DEPOSIT_PENDING,
    DEPOSIT_REFUNDED,
    TattooApplication,
)
from app.services.studio_booking import (
    APPLICATION_TRANSITIONS,
    AppointmentConflictError,
    InvalidApplicationTransitionError,
    InvalidAppointmentTransitionError,
    InvalidDepositTransitionError,
    StudioBookingService,
    _validate_appointment_financials,
    _validate_budget,
)


@pytest.mark.asyncio
async def test_application_transition_updates_history_and_client_lead() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=17,
        status=APPLICATION_QUALIFICATION,
    )
    client = SimpleNamespace(lead_status="qualification")
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=application),
        get=AsyncMock(return_value=client),
        add=MagicMock(),
        commit=AsyncMock(),
    )

    result = await StudioBookingService(db).transition_application(
        application.id,
        APPLICATION_CONSULTATION,
        reason="Бриф заполнен",
    )

    assert result.status == APPLICATION_CONSULTATION
    assert client.lead_status == "consultation"
    history = db.add.call_args.args[0]
    assert history.from_status == APPLICATION_QUALIFICATION
    assert history.to_status == APPLICATION_CONSULTATION
    assert history.reason == "Бриф заполнен"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_application_rejects_skipped_manual_transition() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=17,
        status=APPLICATION_NEW,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=application))

    with pytest.raises(InvalidApplicationTransitionError, match="Cannot move application"):
        await StudioBookingService(db).transition_application(
            application.id,
            APPLICATION_BOOKED,
        )


@pytest.mark.asyncio
async def test_pending_appointment_advances_new_application_through_pipeline() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=23,
        status=APPLICATION_NEW,
    )
    client = SimpleNamespace(lead_status="new")
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[application, None]),
        get=AsyncMock(return_value=client),
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )

    appointment = await StudioBookingService(db).save_appointment(
        application.id,
        scheduled_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
        duration_minutes=180,
        quoted_price=12000,
        deposit_amount=1000,
        status=APPOINTMENT_PENDING,
    )

    assert appointment.status == APPOINTMENT_PENDING
    assert appointment.duration_minutes == 180
    assert application.status == APPLICATION_AWAITING_DEPOSIT
    assert client.lead_status == "waiting_payment"
    history_statuses = [
        call.args[0].to_status
        for call in db.add.call_args_list
        if hasattr(call.args[0], "to_status")
    ]
    assert history_statuses == [
        APPLICATION_QUALIFICATION,
        APPLICATION_CONSULTATION,
        APPLICATION_AWAITING_DEPOSIT,
    ]
    history_entries = [
        call.args[0] for call in db.add.call_args_list if hasattr(call.args[0], "to_status")
    ]
    assert [entry.actor for entry in history_entries] == ["system", "system", "studio"]
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_appointment_overlap_is_translated_before_application_autoflush() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=31,
        status=APPLICATION_NEW,
    )
    db_error = IntegrityError(
        "INSERT INTO appointments",
        {},
        Exception("excl_appointments_no_active_overlap"),
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[application, None]),
        add=MagicMock(),
        flush=AsyncMock(side_effect=db_error),
        rollback=AsyncMock(),
    )

    with pytest.raises(AppointmentConflictError, match="пересекается"):
        await StudioBookingService(db).save_appointment(
            application.id,
            scheduled_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            duration_minutes=120,
            quoted_price=10000,
            deposit_amount=1000,
            status=APPOINTMENT_PENDING,
        )

    db.flush.assert_awaited_once()
    db.rollback.assert_awaited_once()


def test_application_transition_graph_has_terminal_stages() -> None:
    assert APPLICATION_QUALIFICATION in APPLICATION_TRANSITIONS[APPLICATION_NEW]
    assert APPLICATION_AWAITING_DEPOSIT in APPLICATION_TRANSITIONS[APPLICATION_CONSULTATION]
    assert APPLICATION_TRANSITIONS["completed"] == ()
    assert APPLICATION_TRANSITIONS["canceled"] == ()
    assert APPLICATION_TRANSITIONS["rejected"] == ()


def test_budget_validation_rejects_reversed_or_negative_range() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        _validate_budget(-1, 5000)
    with pytest.raises(ValueError, match="cannot be lower"):
        _validate_budget(10000, 5000)


@pytest.mark.asyncio
async def test_canceling_application_cancels_active_appointment() -> None:
    from app.domain.bookings.models import (
        APPLICATION_CANCELED,
        APPOINTMENT_CANCELED,
        APPOINTMENT_CONFIRMED,
    )

    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=41,
        status=APPLICATION_CONSULTATION,
    )
    appointment = SimpleNamespace(
        status=APPOINTMENT_CONFIRMED,
        canceled_at=None,
        canceled_reason=None,
    )
    client = SimpleNamespace(lead_status="consultation")
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[application, appointment]),
        get=AsyncMock(return_value=client),
        add=MagicMock(),
        commit=AsyncMock(),
    )

    await StudioBookingService(db).transition_application(
        application.id,
        APPLICATION_CANCELED,
        reason="Клиент отказался",
    )

    assert application.status == APPLICATION_CANCELED
    assert appointment.status == APPOINTMENT_CANCELED
    assert appointment.canceled_at is not None
    assert appointment.canceled_reason == "Клиент отказался"
    assert client.lead_status == "lost"


@pytest.mark.asyncio
async def test_refund_requires_canceled_appointment() -> None:
    from app.domain.bookings.models import (
        APPOINTMENT_CONFIRMED,
        DEPOSIT_PAID,
        DEPOSIT_REFUNDED,
    )

    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=uuid.uuid4(),
        status=APPOINTMENT_CONFIRMED,
        deposit_status=DEPOSIT_PAID,
        deposit_amount=1_000,
        quoted_price=10_000,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=appointment))

    with pytest.raises(ValueError, match="only after appointment cancellation"):
        await StudioBookingService(db).transition_deposit(
            appointment.id,
            DEPOSIT_REFUNDED,
        )


@pytest.mark.asyncio
async def test_closed_application_cannot_receive_appointment() -> None:
    from app.domain.bookings.models import APPLICATION_CANCELED

    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=52,
        status=APPLICATION_CANCELED,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=application))

    with pytest.raises(InvalidApplicationTransitionError, match="Closed application"):
        await StudioBookingService(db).save_appointment(
            application.id,
            scheduled_start=datetime(2026, 8, 10, 12, 0, tzinfo=UTC),
            duration_minutes=180,
            quoted_price=12000,
            deposit_amount=None,
            status="draft",
        )


@pytest.mark.parametrize(
    ("quoted_price", "deposit_amount", "deposit_status", "appointment_status", "message"),
    [
        (10_000, 1_000, DEPOSIT_NOT_REQUIRED, APPOINTMENT_DRAFT, "must be empty"),
        (10_000, None, DEPOSIT_PENDING, APPOINTMENT_PENDING, "positive deposit"),
        (10_000, 11_000, DEPOSIT_PENDING, APPOINTMENT_PENDING, "cannot exceed"),
        (10_000, 1_000, DEPOSIT_REFUNDED, APPOINTMENT_CANCELED, None),
        (10_000, 1_000, DEPOSIT_REFUNDED, APPOINTMENT_DRAFT, "requires a canceled"),
        (10_000, 1_000, DEPOSIT_PENDING, APPOINTMENT_CONFIRMED, "requires paid"),
    ],
)
def test_appointment_financial_invariants(
    quoted_price: int,
    deposit_amount: int | None,
    deposit_status: str,
    appointment_status: str,
    message: str | None,
) -> None:
    kwargs = {
        "quoted_price": quoted_price,
        "deposit_amount": deposit_amount,
        "deposit_status": deposit_status,
        "appointment_status": appointment_status,
    }
    if message is None:
        _validate_appointment_financials(**kwargs)
    else:
        with pytest.raises(InvalidDepositTransitionError, match=message):
            _validate_appointment_financials(**kwargs)


@pytest.mark.asyncio
async def test_schedule_edit_preserves_deposit_state_and_confirmation_time() -> None:
    confirmed_at = datetime(2026, 7, 1, 10, 30, tzinfo=UTC)
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=61,
        status=APPLICATION_BOOKED,
    )
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=application.id,
        status=APPOINTMENT_CONFIRMED,
        deposit_status=DEPOSIT_PAID,
        deposit_amount=1_000,
        quoted_price=12_000,
        confirmed_at=confirmed_at,
        canceled_at=None,
        canceled_reason=None,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[application, appointment]),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )

    result = await StudioBookingService(db).save_appointment(
        application.id,
        scheduled_start=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
        duration_minutes=240,
        quoted_price=13_000,
        deposit_amount=None,
        status=APPOINTMENT_CONFIRMED,
    )

    assert result.deposit_status == DEPOSIT_PAID
    assert result.deposit_amount == 1_000
    assert result.confirmed_at == confirmed_at
    assert result.quoted_price == 13_000


@pytest.mark.asyncio
async def test_schedule_form_cannot_change_existing_deposit_amount() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=62,
        status=APPLICATION_AWAITING_DEPOSIT,
    )
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=application.id,
        status=APPOINTMENT_PENDING,
        deposit_status=DEPOSIT_PENDING,
        deposit_amount=1_000,
        quoted_price=12_000,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[application, appointment]))

    with pytest.raises(InvalidDepositTransitionError, match="deposit workflow"):
        await StudioBookingService(db).save_appointment(
            application.id,
            scheduled_start=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
            duration_minutes=180,
            quoted_price=12_000,
            deposit_amount=500,
            status=APPOINTMENT_PENDING,
        )


@pytest.mark.asyncio
async def test_deposit_workflow_sets_amount_and_can_waive_it() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=63,
        status=APPLICATION_CONSULTATION,
    )
    client = SimpleNamespace(lead_status="consultation")
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=application.id,
        status=APPOINTMENT_DRAFT,
        deposit_status=DEPOSIT_NOT_REQUIRED,
        deposit_amount=None,
        quoted_price=10_000,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[appointment, application]),
        get=AsyncMock(return_value=client),
        add=MagicMock(),
        commit=AsyncMock(),
    )

    await StudioBookingService(db).transition_deposit(
        appointment.id,
        DEPOSIT_PENDING,
        deposit_amount=1_500,
    )

    assert appointment.deposit_status == DEPOSIT_PENDING
    assert appointment.deposit_amount == 1_500
    assert application.status == APPLICATION_AWAITING_DEPOSIT

    appointment.deposit_status = DEPOSIT_PENDING
    db.scalar = AsyncMock(side_effect=[appointment, application])
    await StudioBookingService(db).transition_deposit(
        appointment.id,
        DEPOSIT_NOT_REQUIRED,
    )

    assert appointment.deposit_status == DEPOSIT_NOT_REQUIRED
    assert appointment.deposit_amount is None


@pytest.mark.asyncio
async def test_paid_canceled_appointment_can_be_refunded() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=64,
        status=APPLICATION_CONSULTATION,
    )
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=application.id,
        status=APPOINTMENT_CANCELED,
        deposit_status=DEPOSIT_PAID,
        deposit_amount=2_000,
        quoted_price=15_000,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[appointment, application]),
        commit=AsyncMock(),
    )

    await StudioBookingService(db).transition_deposit(
        appointment.id,
        DEPOSIT_REFUNDED,
    )

    assert appointment.deposit_status == DEPOSIT_REFUNDED
    assert appointment.deposit_amount == 2_000


@pytest.mark.asyncio
async def test_completed_appointment_cannot_be_edited() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=71,
        status=APPLICATION_BOOKED,
    )
    appointment = SimpleNamespace(status="completed")
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[application, appointment]))

    with pytest.raises(InvalidAppointmentTransitionError, match="Completed appointment"):
        await StudioBookingService(db).save_appointment(
            application.id,
            scheduled_start=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            duration_minutes=120,
            quoted_price=10_000,
            deposit_amount=None,
            status=APPOINTMENT_CONFIRMED,
        )


@pytest.mark.asyncio
async def test_schedule_form_rejects_invalid_appointment_transition() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=72,
        status=APPLICATION_AWAITING_DEPOSIT,
    )
    appointment = SimpleNamespace(
        status=APPOINTMENT_PENDING,
        deposit_status=DEPOSIT_PENDING,
        deposit_amount=1_000,
    )
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[application, appointment]))

    with pytest.raises(InvalidAppointmentTransitionError, match="Cannot move appointment"):
        await StudioBookingService(db).save_appointment(
            application.id,
            scheduled_start=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            duration_minutes=120,
            quoted_price=10_000,
            deposit_amount=None,
            status=APPOINTMENT_DRAFT,
        )


@pytest.mark.asyncio
async def test_new_confirmed_appointment_records_confirmation_time() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=73,
        status=APPLICATION_CONSULTATION,
    )
    client = SimpleNamespace(lead_status="consultation")
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[application, None]),
        get=AsyncMock(return_value=client),
        add=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        refresh=AsyncMock(),
    )

    appointment = await StudioBookingService(db).save_appointment(
        application.id,
        scheduled_start=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        duration_minutes=120,
        quoted_price=10_000,
        deposit_amount=None,
        status=APPOINTMENT_CONFIRMED,
    )

    assert appointment.confirmed_at is not None
    assert appointment.deposit_status == DEPOSIT_NOT_REQUIRED
    assert application.status == APPLICATION_BOOKED


@pytest.mark.asyncio
async def test_valid_appointment_transition_runs_financial_validation() -> None:
    application = TattooApplication(
        id=uuid.uuid4(),
        client_id=74,
        status=APPLICATION_AWAITING_DEPOSIT,
    )
    client = SimpleNamespace(lead_status="waiting_payment")
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=application.id,
        status=APPOINTMENT_PENDING,
        deposit_status=DEPOSIT_PAID,
        deposit_amount=1_000,
        quoted_price=10_000,
        confirmed_at=None,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[appointment, application]),
        get=AsyncMock(return_value=client),
        add=MagicMock(),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )

    result = await StudioBookingService(db).transition_appointment(
        appointment.id,
        APPOINTMENT_CONFIRMED,
    )

    assert result.status == APPOINTMENT_CONFIRMED
    assert result.confirmed_at is not None
    assert application.status == APPLICATION_BOOKED


@pytest.mark.asyncio
async def test_deposit_transition_rejects_unknown_or_disallowed_target() -> None:
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        deposit_status=DEPOSIT_PAID,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=appointment))
    service = StudioBookingService(db)

    with pytest.raises(InvalidDepositTransitionError, match="Unknown deposit"):
        await service.transition_deposit(appointment.id, "chargeback")

    with pytest.raises(InvalidDepositTransitionError, match="Cannot move deposit"):
        await service.transition_deposit(appointment.id, DEPOSIT_PENDING)


@pytest.mark.asyncio
async def test_pending_deposit_requires_amount() -> None:
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=uuid.uuid4(),
        status=APPOINTMENT_DRAFT,
        deposit_status=DEPOSIT_NOT_REQUIRED,
        deposit_amount=None,
        quoted_price=10_000,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=appointment))

    with pytest.raises(InvalidDepositTransitionError, match="positive deposit amount"):
        await StudioBookingService(db).transition_deposit(
            appointment.id,
            DEPOSIT_PENDING,
        )


@pytest.mark.asyncio
async def test_non_pending_transition_cannot_change_deposit_amount() -> None:
    appointment = SimpleNamespace(
        id=uuid.uuid4(),
        application_id=uuid.uuid4(),
        status=APPOINTMENT_PENDING,
        deposit_status=DEPOSIT_PENDING,
        deposit_amount=1_000,
        quoted_price=10_000,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=appointment))

    with pytest.raises(InvalidDepositTransitionError, match="only when deposit becomes pending"):
        await StudioBookingService(db).transition_deposit(
            appointment.id,
            DEPOSIT_PAID,
            deposit_amount=2_000,
        )


@pytest.mark.asyncio
async def test_refund_due_filter_queries_paid_cancellations() -> None:
    result = SimpleNamespace(all=lambda: [])
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    items = await StudioBookingService(db).list_appointments(view="refund_due")

    assert items == []
    query = db.execute.await_args.args[0]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "appointments.status = 'canceled'" in sql
    assert "appointments.deposit_status = 'paid'" in sql


def test_financial_validator_rejects_unknown_deposit_status() -> None:
    with pytest.raises(InvalidDepositTransitionError, match="Unknown deposit"):
        _validate_appointment_financials(
            quoted_price=10_000,
            deposit_amount=None,
            deposit_status="chargeback",
            appointment_status=APPOINTMENT_DRAFT,
        )
