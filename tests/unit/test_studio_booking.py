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
    APPOINTMENT_PENDING,
    DEPOSIT_PENDING,
    TattooApplication,
)
from app.services.studio_booking import (
    APPLICATION_TRANSITIONS,
    AppointmentConflictError,
    InvalidApplicationTransitionError,
    StudioBookingService,
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
        deposit_status=DEPOSIT_PENDING,
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
            deposit_status="paid",
            status="confirmed",
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
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=appointment))

    with pytest.raises(ValueError, match="only after appointment cancellation"):
        await StudioBookingService(db).transition_deposit(
            appointment.id,
            DEPOSIT_REFUNDED,
        )


@pytest.mark.asyncio
async def test_closed_application_cannot_receive_appointment() -> None:
    from app.domain.bookings.models import APPLICATION_CANCELED, DEPOSIT_NOT_REQUIRED

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
            deposit_status=DEPOSIT_NOT_REQUIRED,
            status="draft",
        )
