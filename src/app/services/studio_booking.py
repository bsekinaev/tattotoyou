"""Продуктовый слой заявок на татуировку и записей на сеанс."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.domain.bookings.models import (
    ACTIVE_APPLICATION_STATUSES,
    APPLICATION_AWAITING_DEPOSIT,
    APPLICATION_BOOKED,
    APPLICATION_CANCELED,
    APPLICATION_COMPLETED,
    APPLICATION_CONSULTATION,
    APPLICATION_NEW,
    APPLICATION_QUALIFICATION,
    APPLICATION_REJECTED,
    APPLICATION_STATUSES,
    APPOINTMENT_CANCELED,
    APPOINTMENT_COMPLETED,
    APPOINTMENT_CONFIRMED,
    APPOINTMENT_DRAFT,
    APPOINTMENT_PENDING,
    APPOINTMENT_STATUSES,
    COLOR_MODES,
    DEPOSIT_NOT_REQUIRED,
    DEPOSIT_PAID,
    DEPOSIT_PENDING,
    DEPOSIT_REFUNDED,
    DEPOSIT_STATUSES,
    REFERENCE_TYPES,
    ApplicationReference,
    ApplicationStatusHistory,
    Appointment,
    TattooApplication,
)
from app.domain.clients.models import Client, Platform
from app.domain.conversations.models import Conversation

APPLICATION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    APPLICATION_NEW: (
        APPLICATION_QUALIFICATION,
        APPLICATION_CANCELED,
        APPLICATION_REJECTED,
    ),
    APPLICATION_QUALIFICATION: (
        APPLICATION_CONSULTATION,
        APPLICATION_CANCELED,
        APPLICATION_REJECTED,
    ),
    APPLICATION_CONSULTATION: (
        APPLICATION_AWAITING_DEPOSIT,
        APPLICATION_CANCELED,
        APPLICATION_REJECTED,
    ),
    APPLICATION_AWAITING_DEPOSIT: (
        APPLICATION_BOOKED,
        APPLICATION_CONSULTATION,
        APPLICATION_CANCELED,
        APPLICATION_REJECTED,
    ),
    APPLICATION_BOOKED: (APPLICATION_COMPLETED, APPLICATION_CANCELED),
    APPLICATION_COMPLETED: (),
    APPLICATION_CANCELED: (),
    APPLICATION_REJECTED: (),
}

APPOINTMENT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    APPOINTMENT_DRAFT: (
        APPOINTMENT_PENDING,
        APPOINTMENT_CONFIRMED,
        APPOINTMENT_CANCELED,
    ),
    APPOINTMENT_PENDING: (APPOINTMENT_CONFIRMED, APPOINTMENT_CANCELED),
    APPOINTMENT_CONFIRMED: (APPOINTMENT_COMPLETED, APPOINTMENT_CANCELED),
    APPOINTMENT_COMPLETED: (),
    APPOINTMENT_CANCELED: (APPOINTMENT_DRAFT,),
}

DEPOSIT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    DEPOSIT_NOT_REQUIRED: (DEPOSIT_PENDING,),
    DEPOSIT_PENDING: (DEPOSIT_PAID, DEPOSIT_NOT_REQUIRED),
    DEPOSIT_PAID: (DEPOSIT_REFUNDED,),
    DEPOSIT_REFUNDED: (DEPOSIT_PENDING, DEPOSIT_NOT_REQUIRED),
}

APPLICATION_FILTERS = ("active", *APPLICATION_STATUSES, "all")
APPOINTMENT_FILTERS = ("upcoming", "awaiting_deposit", "refund_due", "history", "all")

LEAD_STATUS_BY_APPLICATION_STATUS = {
    APPLICATION_NEW: "new",
    APPLICATION_QUALIFICATION: "qualification",
    APPLICATION_CONSULTATION: "consultation",
    APPLICATION_AWAITING_DEPOSIT: "waiting_payment",
    APPLICATION_BOOKED: "booked",
    APPLICATION_COMPLETED: "completed",
    APPLICATION_CANCELED: "lost",
    APPLICATION_REJECTED: "lost",
}


class InvalidApplicationTransitionError(ValueError):
    """Переход заявки нарушает продуктовую state machine."""


class InvalidAppointmentTransitionError(ValueError):
    """Переход записи нарушает state machine."""


class AppointmentConflictError(ValueError):
    """Активная запись пересекается с другим сеансом."""


class InvalidDepositTransitionError(ValueError):
    """Переход или финансовое состояние предоплаты некорректны."""


@dataclass(slots=True)
class ApplicationListItem:
    application: TattooApplication
    client: Client
    platform: Platform
    appointment: Appointment | None


@dataclass(slots=True)
class ApplicationDetail:
    application: TattooApplication
    client: Client
    platform: Platform
    conversation: Conversation | None
    references: list[ApplicationReference]
    history: list[ApplicationStatusHistory]
    appointment: Appointment | None
    allowed_transitions: tuple[str, ...]


@dataclass(slots=True)
class AppointmentListItem:
    appointment: Appointment
    application: TattooApplication
    client: Client
    platform: Platform


class StudioBookingService:
    """Операции над заявками, референсами и записью клиента."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_application_from_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> TattooApplication:
        conversation = await self.db.scalar(
            select(Conversation).where(Conversation.id == conversation_id).with_for_update()
        )
        if conversation is None:
            raise LookupError("Conversation does not exist")
        client = await self.db.get(Client, conversation.client_id)
        if client is None:
            raise LookupError("Conversation client does not exist")

        existing = await self.db.scalar(
            select(TattooApplication).where(
                TattooApplication.conversation_id == conversation_id,
                TattooApplication.status.in_(ACTIVE_APPLICATION_STATUSES),
            )
        )
        if existing is not None:
            return existing

        application = TattooApplication(
            client_id=client.id,
            conversation_id=conversation.id,
            status=APPLICATION_NEW,
            idea=client.tattoo_idea,
            placement=client.placement,
            size_details=client.size_details,
            style_preferences=client.style_preferences,
            desired_date=client.desired_date,
            internal_notes=client.internal_notes,
        )
        application.status_history.append(
            ApplicationStatusHistory(
                from_status=None,
                to_status=APPLICATION_NEW,
                actor="studio",
                reason="Заявка создана из диалога",
            )
        )
        self.db.add(application)
        client.lead_status = "new"
        await self.db.commit()
        await self.db.refresh(application)
        return application

    async def list_applications(
        self,
        *,
        status_filter: str = "active",
        search: str | None = None,
        limit: int = 200,
    ) -> list[ApplicationListItem]:
        if status_filter not in APPLICATION_FILTERS:
            status_filter = "active"

        query = (
            select(TattooApplication, Client, Platform, Appointment)
            .join(Client, Client.id == TattooApplication.client_id)
            .join(Platform, Platform.id == Client.platform_id)
            .outerjoin(Appointment, Appointment.application_id == TattooApplication.id)
            .order_by(
                case(
                    (TattooApplication.status == APPLICATION_AWAITING_DEPOSIT, 0),
                    (TattooApplication.status == APPLICATION_CONSULTATION, 1),
                    (TattooApplication.status == APPLICATION_QUALIFICATION, 2),
                    (TattooApplication.status == APPLICATION_NEW, 3),
                    (TattooApplication.status == APPLICATION_BOOKED, 4),
                    else_=5,
                ),
                TattooApplication.updated_at.desc(),
            )
            .limit(limit)
        )
        if status_filter == "active":
            query = query.where(TattooApplication.status.in_(ACTIVE_APPLICATION_STATUSES))
        elif status_filter != "all":
            query = query.where(TattooApplication.status == status_filter)

        normalized_search = (search or "").strip()
        if normalized_search:
            pattern = f"%{normalized_search}%"
            query = query.where(
                or_(
                    Client.display_name.ilike(pattern),
                    Client.username.ilike(pattern),
                    TattooApplication.idea.ilike(pattern),
                    TattooApplication.placement.ilike(pattern),
                    TattooApplication.internal_notes.ilike(pattern),
                )
            )

        rows = (await self.db.execute(query)).all()
        return [
            ApplicationListItem(
                application=application,
                client=client,
                platform=platform,
                appointment=appointment,
            )
            for application, client, platform, appointment in rows
        ]

    async def get_application_detail(self, application_id: uuid.UUID) -> ApplicationDetail:
        result = await self.db.execute(
            select(TattooApplication)
            .where(TattooApplication.id == application_id)
            .options(
                joinedload(TattooApplication.client).joinedload(Client.platform),
                joinedload(TattooApplication.conversation),
                joinedload(TattooApplication.appointment),
                selectinload(TattooApplication.references),
                selectinload(TattooApplication.status_history),
            )
        )
        application = result.scalar_one_or_none()
        if application is None:
            raise LookupError("Application does not exist")

        return ApplicationDetail(
            application=application,
            client=application.client,
            platform=application.client.platform,
            conversation=application.conversation,
            references=list(application.references),
            history=list(application.status_history),
            appointment=application.appointment,
            allowed_transitions=APPLICATION_TRANSITIONS[application.status],
        )

    async def update_application(
        self,
        application_id: uuid.UUID,
        *,
        idea: str | None,
        placement: str | None,
        size_details: str | None,
        color_mode: str,
        style_preferences: str | None,
        budget_min: int | None,
        budget_max: int | None,
        desired_date: date | None,
        client_comment: str | None,
        internal_notes: str | None,
    ) -> TattooApplication:
        if color_mode not in COLOR_MODES:
            raise ValueError("Unknown color mode")
        _validate_budget(budget_min, budget_max)

        application = await self._locked_application(application_id)
        application.idea = _optional_text(idea, 5000)
        application.placement = _optional_text(placement, 120)
        application.size_details = _optional_text(size_details, 120)
        application.color_mode = color_mode
        application.style_preferences = _optional_text(style_preferences, 200)
        application.budget_min = budget_min
        application.budget_max = budget_max
        application.desired_date = desired_date
        application.client_comment = _optional_text(client_comment, 5000)
        application.internal_notes = _optional_text(internal_notes, 5000)

        client = await self.db.get(Client, application.client_id)
        if client is not None:
            client.tattoo_idea = application.idea
            client.placement = application.placement
            client.size_details = application.size_details
            client.style_preferences = application.style_preferences
            client.desired_date = application.desired_date

        await self.db.commit()
        return application

    async def transition_application(
        self,
        application_id: uuid.UUID,
        target_status: str,
        *,
        reason: str | None = None,
    ) -> TattooApplication:
        application = await self._locked_application(application_id)
        await self._apply_application_transition(application, target_status, reason=reason)
        await self.db.commit()
        return application

    async def add_reference(
        self,
        application_id: uuid.UUID,
        *,
        reference_type: str,
        value: str,
        file_unique_id: str | None = None,
        file_name: str | None = None,
        caption: str | None = None,
        source_message_id: str | None = None,
    ) -> ApplicationReference:
        if reference_type not in REFERENCE_TYPES:
            raise ValueError("Unknown reference type")
        normalized_value = _required_text(value, 2048)
        if reference_type == "url" and not normalized_value.startswith(("https://", "http://")):
            raise ValueError("Reference URL must start with http:// or https://")

        await self._locked_application(application_id)
        reference = ApplicationReference(
            application_id=application_id,
            reference_type=reference_type,
            value=normalized_value,
            file_unique_id=_optional_text(file_unique_id, 255),
            file_name=_optional_text(file_name, 255),
            caption=_optional_text(caption, 2000),
            source_message_id=_optional_text(source_message_id, 100),
        )
        self.db.add(reference)
        await self.db.commit()
        await self.db.refresh(reference)
        return reference

    async def save_appointment(
        self,
        application_id: uuid.UUID,
        *,
        scheduled_start: datetime,
        duration_minutes: int,
        quoted_price: int | None,
        deposit_amount: int | None,
        status: str,
    ) -> Appointment:
        if scheduled_start.tzinfo is None or scheduled_start.utcoffset() is None:
            raise ValueError("Appointment start must be timezone-aware")
        if duration_minutes <= 0 or duration_minutes > 1440:
            raise ValueError("Appointment duration must be between 1 and 1440 minutes")
        _validate_money(quoted_price, "Quoted price")
        _validate_money(deposit_amount, "Deposit amount")
        if status not in (APPOINTMENT_DRAFT, APPOINTMENT_PENDING, APPOINTMENT_CONFIRMED):
            raise ValueError("Unsupported appointment status for schedule form")

        application = await self._locked_application(application_id)
        if application.status in (
            APPLICATION_COMPLETED,
            APPLICATION_CANCELED,
            APPLICATION_REJECTED,
        ):
            raise InvalidApplicationTransitionError(
                "Closed application cannot receive or edit an appointment"
            )
        appointment = await self.db.scalar(
            select(Appointment)
            .where(Appointment.application_id == application.id)
            .with_for_update()
        )
        is_new = appointment is None
        if is_new:
            normalized_deposit_amount = deposit_amount if deposit_amount not in (None, 0) else None
            deposit_status = (
                DEPOSIT_PENDING if normalized_deposit_amount is not None else DEPOSIT_NOT_REQUIRED
            )
            appointment = Appointment(
                application_id=application.id,
                deposit_amount=normalized_deposit_amount,
                deposit_status=deposit_status,
            )
            self.db.add(appointment)
            previous_status: str | None = None
        else:
            previous_status = appointment.status
            if appointment.status == APPOINTMENT_COMPLETED:
                raise InvalidAppointmentTransitionError("Completed appointment cannot be edited")
            if (
                status != appointment.status
                and status not in APPOINTMENT_TRANSITIONS[appointment.status]
            ):
                raise InvalidAppointmentTransitionError(
                    f"Cannot move appointment from {appointment.status} to {status}"
                )
            if deposit_amount is not None and deposit_amount != appointment.deposit_amount:
                raise InvalidDepositTransitionError(
                    "Deposit amount can be changed only through the deposit workflow"
                )

        _validate_appointment_financials(
            quoted_price=quoted_price,
            deposit_amount=appointment.deposit_amount,
            deposit_status=appointment.deposit_status,
            appointment_status=status,
        )

        appointment.scheduled_start = scheduled_start.astimezone(UTC)
        appointment.duration_minutes = duration_minutes
        appointment.scheduled_end = appointment.scheduled_start + timedelta(
            minutes=duration_minutes
        )
        appointment.quoted_price = quoted_price
        appointment.status = status
        appointment.canceled_reason = None
        appointment.canceled_at = None
        if status != APPOINTMENT_CONFIRMED:
            appointment.confirmed_at = None

        try:
            # Force PostgreSQL exclusion constraints before any query can trigger
            # an implicit autoflush while the application pipeline is advanced.
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            if "excl_appointments_no_active_overlap" in str(exc.orig):
                raise AppointmentConflictError(
                    "Выбранное время пересекается с другой активной записью"
                ) from exc
            raise

        if status == APPOINTMENT_PENDING:
            await self._move_application_for_appointment(
                application,
                APPLICATION_AWAITING_DEPOSIT,
                reason="Время сеанса ожидает подтверждения предоплаты",
            )
        elif status == APPOINTMENT_CONFIRMED:
            if previous_status != APPOINTMENT_CONFIRMED:
                appointment.confirmed_at = datetime.now(UTC)
            await self._move_application_for_appointment(
                application,
                APPLICATION_BOOKED,
                reason="Сеанс подтверждён",
            )

        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            if "excl_appointments_no_active_overlap" in str(exc.orig):
                raise AppointmentConflictError(
                    "Выбранное время пересекается с другой активной записью"
                ) from exc
            raise
        await self.db.refresh(appointment)
        return appointment

    async def transition_appointment(
        self,
        appointment_id: uuid.UUID,
        target_status: str,
        *,
        reason: str | None = None,
    ) -> Appointment:
        if target_status not in APPOINTMENT_STATUSES:
            raise InvalidAppointmentTransitionError("Unknown appointment status")

        appointment = await self._locked_appointment(appointment_id)
        if target_status not in APPOINTMENT_TRANSITIONS[appointment.status]:
            raise InvalidAppointmentTransitionError(
                f"Cannot move appointment from {appointment.status} to {target_status}"
            )
        if target_status == APPOINTMENT_CONFIRMED and appointment.deposit_status not in (
            DEPOSIT_PAID,
            DEPOSIT_NOT_REQUIRED,
        ):
            raise InvalidAppointmentTransitionError(
                "Confirming an appointment requires paid or waived deposit"
            )

        _validate_appointment_financials(
            quoted_price=appointment.quoted_price,
            deposit_amount=appointment.deposit_amount,
            deposit_status=appointment.deposit_status,
            appointment_status=target_status,
        )

        application = await self._locked_application(appointment.application_id)
        appointment.status = target_status
        now = datetime.now(UTC)

        if target_status == APPOINTMENT_DRAFT:
            appointment.canceled_at = None
            appointment.canceled_reason = None
            appointment.confirmed_at = None
            await self._move_application_for_appointment(
                application,
                APPLICATION_CONSULTATION,
                reason="Запись возвращена в черновик",
            )
        elif target_status == APPOINTMENT_PENDING:
            await self._move_application_for_appointment(
                application,
                APPLICATION_AWAITING_DEPOSIT,
                reason="Запись ожидает подтверждения",
            )
        elif target_status == APPOINTMENT_CONFIRMED:
            appointment.confirmed_at = now
            await self._move_application_for_appointment(
                application,
                APPLICATION_BOOKED,
                reason="Сеанс подтверждён",
            )
        elif target_status == APPOINTMENT_COMPLETED:
            appointment.completed_at = now
            await self._move_application_for_appointment(
                application,
                APPLICATION_COMPLETED,
                reason="Сеанс завершён",
            )
        elif target_status == APPOINTMENT_CANCELED:
            appointment.canceled_at = now
            appointment.canceled_reason = _required_text(reason, 2000)
            await self._move_application_for_appointment(
                application,
                APPLICATION_CONSULTATION,
                reason="Запись отменена; проект возвращён на консультацию",
            )

        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            if "excl_appointments_no_active_overlap" in str(exc.orig):
                raise AppointmentConflictError(
                    "Выбранное время пересекается с другой активной записью"
                ) from exc
            raise
        return appointment

    async def transition_deposit(
        self,
        appointment_id: uuid.UUID,
        target_status: str,
        *,
        deposit_amount: int | None = None,
    ) -> Appointment:
        if target_status not in DEPOSIT_STATUSES:
            raise InvalidDepositTransitionError("Unknown deposit status")
        appointment = await self._locked_appointment(appointment_id)
        if target_status not in DEPOSIT_TRANSITIONS[appointment.deposit_status]:
            raise InvalidDepositTransitionError(
                f"Cannot move deposit from {appointment.deposit_status} to {target_status}"
            )

        next_amount = appointment.deposit_amount
        if target_status == DEPOSIT_PENDING:
            if deposit_amount is not None:
                _validate_money(deposit_amount, "Deposit amount")
                next_amount = deposit_amount
            if next_amount is None or next_amount <= 0:
                raise InvalidDepositTransitionError(
                    "Pending deposit requires a positive deposit amount"
                )
        elif deposit_amount is not None and deposit_amount != appointment.deposit_amount:
            raise InvalidDepositTransitionError(
                "Deposit amount can be changed only when deposit becomes pending"
            )

        if target_status == DEPOSIT_NOT_REQUIRED:
            next_amount = None
        if target_status == DEPOSIT_REFUNDED and appointment.status != APPOINTMENT_CANCELED:
            raise InvalidDepositTransitionError(
                "Deposit can be refunded only after appointment cancellation"
            )

        _validate_appointment_financials(
            quoted_price=appointment.quoted_price,
            deposit_amount=next_amount,
            deposit_status=target_status,
            appointment_status=appointment.status,
        )

        appointment.deposit_status = target_status
        appointment.deposit_amount = next_amount
        application = await self._locked_application(appointment.application_id)
        if target_status == DEPOSIT_PENDING:
            await self._move_application_for_appointment(
                application,
                APPLICATION_AWAITING_DEPOSIT,
                reason="Ожидается предоплата",
            )
        await self.db.commit()
        return appointment

    async def list_appointments(
        self,
        *,
        view: str = "upcoming",
        limit: int = 200,
    ) -> list[AppointmentListItem]:
        if view not in APPOINTMENT_FILTERS:
            view = "upcoming"

        now = datetime.now(UTC)
        query = (
            select(Appointment, TattooApplication, Client, Platform)
            .join(TattooApplication, TattooApplication.id == Appointment.application_id)
            .join(Client, Client.id == TattooApplication.client_id)
            .join(Platform, Platform.id == Client.platform_id)
            .order_by(Appointment.scheduled_start.asc())
            .limit(limit)
        )
        if view == "upcoming":
            query = query.where(
                Appointment.status.in_((APPOINTMENT_PENDING, APPOINTMENT_CONFIRMED)),
                Appointment.scheduled_end >= now,
            )
        elif view == "awaiting_deposit":
            query = query.where(
                Appointment.deposit_status == DEPOSIT_PENDING,
                Appointment.status.in_((APPOINTMENT_DRAFT, APPOINTMENT_PENDING)),
            )
        elif view == "refund_due":
            query = query.where(
                Appointment.status == APPOINTMENT_CANCELED,
                Appointment.deposit_status == DEPOSIT_PAID,
            )
        elif view == "history":
            query = query.where(
                or_(
                    Appointment.status.in_((APPOINTMENT_COMPLETED, APPOINTMENT_CANCELED)),
                    Appointment.scheduled_end < now,
                )
            )

        rows = (await self.db.execute(query)).all()
        return [
            AppointmentListItem(
                appointment=appointment,
                application=application,
                client=client,
                platform=platform,
            )
            for appointment, application, client, platform in rows
        ]

    async def _locked_application(self, application_id: uuid.UUID) -> TattooApplication:
        application = await self.db.scalar(
            select(TattooApplication)
            .where(TattooApplication.id == application_id)
            .with_for_update()
        )
        if application is None:
            raise LookupError("Application does not exist")
        return application

    async def _locked_appointment(self, appointment_id: uuid.UUID) -> Appointment:
        appointment = await self.db.scalar(
            select(Appointment).where(Appointment.id == appointment_id).with_for_update()
        )
        if appointment is None:
            raise LookupError("Appointment does not exist")
        return appointment

    async def _apply_application_transition(
        self,
        application: TattooApplication,
        target_status: str,
        *,
        reason: str | None,
    ) -> None:
        if target_status not in APPLICATION_STATUSES:
            raise InvalidApplicationTransitionError("Unknown application status")
        if target_status not in APPLICATION_TRANSITIONS[application.status]:
            raise InvalidApplicationTransitionError(
                f"Cannot move application from {application.status} to {target_status}"
            )
        appointment: Appointment | None = None
        if target_status in (
            APPLICATION_BOOKED,
            APPLICATION_COMPLETED,
            APPLICATION_CANCELED,
            APPLICATION_REJECTED,
        ):
            appointment = await self.db.scalar(
                select(Appointment)
                .where(Appointment.application_id == application.id)
                .with_for_update()
            )
        if target_status == APPLICATION_BOOKED and (
            appointment is None or appointment.status != APPOINTMENT_CONFIRMED
        ):
            raise InvalidApplicationTransitionError(
                "Booked application requires confirmed appointment"
            )
        if target_status == APPLICATION_COMPLETED and (
            appointment is None or appointment.status != APPOINTMENT_COMPLETED
        ):
            raise InvalidApplicationTransitionError(
                "Completed application requires completed appointment"
            )
        if target_status in (APPLICATION_CANCELED, APPLICATION_REJECTED):
            normalized_reason = _required_text(reason, 2000)
            if appointment is not None:
                if appointment.status == APPOINTMENT_COMPLETED:
                    raise InvalidApplicationTransitionError(
                        "Application with completed appointment cannot be closed as lost"
                    )
                if appointment.status != APPOINTMENT_CANCELED:
                    appointment.status = APPOINTMENT_CANCELED
                    appointment.canceled_at = datetime.now(UTC)
                    appointment.canceled_reason = normalized_reason
            reason = normalized_reason

        await self._set_application_status(application, target_status, reason=reason)

    async def _move_application_for_appointment(
        self,
        application: TattooApplication,
        target_status: str,
        *,
        reason: str,
    ) -> None:
        if application.status == target_status:
            return
        if target_status == APPLICATION_CONSULTATION and application.status in (
            APPLICATION_AWAITING_DEPOSIT,
            APPLICATION_BOOKED,
        ):
            await self._set_application_status(application, target_status, reason=reason)
            return

        forward_path = {
            APPLICATION_NEW: APPLICATION_QUALIFICATION,
            APPLICATION_QUALIFICATION: APPLICATION_CONSULTATION,
            APPLICATION_CONSULTATION: APPLICATION_AWAITING_DEPOSIT,
            APPLICATION_AWAITING_DEPOSIT: APPLICATION_BOOKED,
            APPLICATION_BOOKED: APPLICATION_COMPLETED,
        }
        while application.status != target_status:
            next_status = forward_path.get(application.status)
            if next_status is None:
                raise InvalidApplicationTransitionError(
                    f"Cannot align application {application.status} with appointment {target_status}"
                )
            is_automatic_step = next_status != target_status
            step_reason = reason if not is_automatic_step else "Этап создан автоматически"
            await self._set_application_status(
                application,
                next_status,
                reason=step_reason,
                actor="system" if is_automatic_step else "studio",
            )

    async def _set_application_status(
        self,
        application: TattooApplication,
        target_status: str,
        *,
        reason: str | None,
        actor: str = "studio",
    ) -> None:
        previous = application.status
        application.status = target_status
        application.closed_at = (
            datetime.now(UTC)
            if target_status in (APPLICATION_COMPLETED, APPLICATION_CANCELED, APPLICATION_REJECTED)
            else None
        )
        self.db.add(
            ApplicationStatusHistory(
                application_id=application.id,
                from_status=previous,
                to_status=target_status,
                actor=actor,
                reason=_optional_text(reason, 2000),
            )
        )
        client = await self.db.get(Client, application.client_id)
        if client is not None:
            client.lead_status = LEAD_STATUS_BY_APPLICATION_STATUS[target_status]


def _optional_text(value: str | None, max_length: int) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise ValueError(f"Value exceeds {max_length} characters")
    return normalized


def _required_text(value: str | None, max_length: int) -> str:
    normalized = _optional_text(value, max_length)
    if normalized is None:
        raise ValueError("Value is required")
    return normalized


def _validate_appointment_financials(
    *,
    quoted_price: int | None,
    deposit_amount: int | None,
    deposit_status: str,
    appointment_status: str,
) -> None:
    _validate_money(quoted_price, "Quoted price")
    _validate_money(deposit_amount, "Deposit amount")
    if deposit_status not in DEPOSIT_STATUSES:
        raise InvalidDepositTransitionError("Unknown deposit status")
    if deposit_status == DEPOSIT_NOT_REQUIRED:
        if deposit_amount not in (None, 0):
            raise InvalidDepositTransitionError(
                "Deposit amount must be empty when deposit is not required"
            )
    elif deposit_amount is None or deposit_amount <= 0:
        raise InvalidDepositTransitionError(
            f"Deposit status {deposit_status} requires a positive deposit amount"
        )
    if quoted_price is not None and deposit_amount is not None and deposit_amount > quoted_price:
        raise InvalidDepositTransitionError("Deposit amount cannot exceed the quoted price")
    if deposit_status == DEPOSIT_REFUNDED and appointment_status != APPOINTMENT_CANCELED:
        raise InvalidDepositTransitionError("Refunded deposit requires a canceled appointment")
    if appointment_status in (
        APPOINTMENT_CONFIRMED,
        APPOINTMENT_COMPLETED,
    ) and deposit_status not in (
        DEPOSIT_PAID,
        DEPOSIT_NOT_REQUIRED,
    ):
        raise InvalidDepositTransitionError(
            "Confirmed or completed appointment requires paid or waived deposit"
        )


def _validate_budget(budget_min: int | None, budget_max: int | None) -> None:
    _validate_money(budget_min, "Minimum budget")
    _validate_money(budget_max, "Maximum budget")
    if budget_min is not None and budget_max is not None and budget_max < budget_min:
        raise ValueError("Maximum budget cannot be lower than minimum budget")


def _validate_money(value: int | None, label: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{label} cannot be negative")
