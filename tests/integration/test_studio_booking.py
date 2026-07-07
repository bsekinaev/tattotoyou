"""PostgreSQL integration tests for tattoo applications and appointment scheduling."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.bookings.models import (
    APPLICATION_COMPLETED,
    APPOINTMENT_COMPLETED,
    APPOINTMENT_CONFIRMED,
    APPOINTMENT_PENDING,
    DEPOSIT_PAID,
    DEPOSIT_PENDING,
    ApplicationStatusHistory,
    Appointment,
    TattooApplication,
)
from app.domain.clients.models import Client, Platform
from app.infrastructure.db.repositories import (
    ClientRepository,
    ConversationRepository,
    PlatformRepository,
)
from app.services.studio_booking import AppointmentConflictError, StudioBookingService

pytestmark = pytest.mark.integration


def _test_dsn() -> str:
    dsn = os.getenv("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


async def _assert_schema_ready(engine) -> None:
    async with engine.connect() as connection:
        application_table = await connection.scalar(
            text("SELECT to_regclass('public.tattoo_applications')")
        )
        appointment_table = await connection.scalar(
            text("SELECT to_regclass('public.appointments')")
        )
        exclusion_constraint = await connection.scalar(
            text(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'excl_appointments_no_active_overlap'
                """
            )
        )
        if application_table is None or appointment_table is None or exclusion_constraint is None:
            pytest.fail(
                "PostgreSQL schema does not contain the booking revision. "
                "Run `python -m alembic upgrade head` before integration tests."
            )


async def _create_conversation(session_factory, *, platform_name: str, suffix: str):
    async with session_factory() as session, session.begin():
        platform = await PlatformRepository(session).get_or_create(name=platform_name)
        client = await ClientRepository(session).get_or_create(
            platform_id=platform.id,
            external_id=f"client-{suffix}",
            display_name=f"Booking {suffix[:8]}",
        )
        conversation = await ConversationRepository(session).get_or_create_active(client.id)
        return platform.id, client.id, conversation.id


@pytest.mark.asyncio
async def test_application_lifecycle_reaches_completed_booking() -> None:
    engine = create_async_engine(_test_dsn(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    platform_name = f"booking-{suffix[:8]}"
    platform_id: int | None = None

    try:
        await _assert_schema_ready(engine)
        platform_id, client_id, conversation_id = await _create_conversation(
            session_factory,
            platform_name=platform_name,
            suffix=suffix,
        )

        async with session_factory() as session:
            service = StudioBookingService(session)
            application = await service.create_application_from_conversation(conversation_id)
            application_id = application.id
            await service.update_application(
                application_id,
                idea="Графический ворон",
                placement="Предплечье",
                size_details="15 x 10 см",
                color_mode="black_and_grey",
                style_preferences="Графика",
                budget_min=10_000,
                budget_max=15_000,
                desired_date=None,
                client_comment="Хочется тонкие линии",
                internal_notes="Попросить два референса",
            )
            appointment = await service.save_appointment(
                application_id,
                scheduled_start=datetime.now(UTC) + timedelta(days=7),
                duration_minutes=180,
                quoted_price=12_000,
                deposit_amount=1_000,
                deposit_status=DEPOSIT_PENDING,
                status=APPOINTMENT_PENDING,
            )
            appointment_id = appointment.id
            await service.transition_deposit(appointment_id, DEPOSIT_PAID)
            await service.transition_appointment(appointment_id, APPOINTMENT_CONFIRMED)
            await service.transition_appointment(appointment_id, APPOINTMENT_COMPLETED)

        async with session_factory() as session:
            application = await session.get(TattooApplication, application_id)
            appointment = await session.get(Appointment, appointment_id)
            client = await session.get(Client, client_id)
            history = (
                await session.scalars(
                    select(ApplicationStatusHistory)
                    .where(ApplicationStatusHistory.application_id == application_id)
                    .order_by(ApplicationStatusHistory.id)
                )
            ).all()

        assert application is not None
        assert application.status == APPLICATION_COMPLETED
        assert application.idea == "Графический ворон"
        assert appointment is not None
        assert appointment.status == APPOINTMENT_COMPLETED
        assert appointment.deposit_status == DEPOSIT_PAID
        assert appointment.completed_at is not None
        assert client is not None
        assert client.lead_status == "completed"
        assert [entry.to_status for entry in history] == [
            "new",
            "qualification",
            "consultation",
            "awaiting_deposit",
            "booked",
            "completed",
        ]
    finally:
        try:
            if platform_id is not None:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(Platform).where(Platform.id == platform_id))
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_active_appointments_cannot_overlap() -> None:
    engine = create_async_engine(_test_dsn(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    platform_names = [f"booking-a-{suffix[:10]}", f"booking-b-{suffix[:10]}"]
    platform_ids: list[int] = []

    try:
        await _assert_schema_ready(engine)
        conversations = []
        for index, platform_name in enumerate(platform_names):
            platform_id, _client_id, conversation_id = await _create_conversation(
                session_factory,
                platform_name=platform_name,
                suffix=f"{suffix}-{index}",
            )
            platform_ids.append(platform_id)
            conversations.append(conversation_id)

        start = datetime.now(UTC) + timedelta(days=14)

        async with session_factory() as session:
            service = StudioBookingService(session)
            first = await service.create_application_from_conversation(conversations[0])
            await service.save_appointment(
                first.id,
                scheduled_start=start,
                duration_minutes=180,
                quoted_price=15_000,
                deposit_amount=1_000,
                deposit_status=DEPOSIT_PAID,
                status=APPOINTMENT_CONFIRMED,
            )

        async with session_factory() as session:
            service = StudioBookingService(session)
            second = await service.create_application_from_conversation(conversations[1])
            with pytest.raises(AppointmentConflictError):
                await service.save_appointment(
                    second.id,
                    scheduled_start=start + timedelta(minutes=60),
                    duration_minutes=120,
                    quoted_price=10_000,
                    deposit_amount=1_000,
                    deposit_status=DEPOSIT_PAID,
                    status=APPOINTMENT_CONFIRMED,
                )

        async with session_factory() as session:
            appointments = (
                await session.scalars(
                    select(Appointment).where(
                        Appointment.status == APPOINTMENT_CONFIRMED,
                        Appointment.scheduled_start >= start,
                        Appointment.scheduled_start < start + timedelta(minutes=1),
                    )
                )
            ).all()
        assert len(appointments) == 1
    finally:
        try:
            if platform_ids:
                async with session_factory() as session, session.begin():
                    await session.execute(delete(Platform).where(Platform.id.in_(platform_ids)))
        finally:
            await engine.dispose()
