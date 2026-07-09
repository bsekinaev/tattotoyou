"""HTML-панель заявок на татуировку и управления записью."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.studio.auth import (
    create_csrf_token,
    require_studio_csrf,
    require_studio_session,
)
from app.core.config import Settings, get_settings
from app.domain.bookings.models import (
    APPLICATION_AWAITING_DEPOSIT,
    APPLICATION_BOOKED,
    APPLICATION_CANCELED,
    APPLICATION_COMPLETED,
    APPLICATION_CONSULTATION,
    APPLICATION_NEW,
    APPLICATION_QUALIFICATION,
    APPLICATION_REJECTED,
    APPOINTMENT_CANCELED,
    APPOINTMENT_COMPLETED,
    APPOINTMENT_CONFIRMED,
    APPOINTMENT_DRAFT,
    APPOINTMENT_PENDING,
    COLOR_BLACK_AND_GREY,
    COLOR_COLOR,
    COLOR_UNDECIDED,
    DEPOSIT_NOT_REQUIRED,
    DEPOSIT_PAID,
    DEPOSIT_PENDING,
    DEPOSIT_REFUNDED,
)
from app.infrastructure.db.session import get_db_session
from app.services.studio_booking import (
    APPLICATION_FILTERS,
    APPOINTMENT_FILTERS,
    AppointmentConflictError,
    InvalidApplicationTransitionError,
    InvalidAppointmentTransitionError,
    StudioBookingService,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))
STUDIO_TIMEZONE = ZoneInfo("Europe/Moscow")

APPLICATION_STATUS_LABELS = {
    APPLICATION_NEW: "Новая",
    APPLICATION_QUALIFICATION: "Уточнение",
    APPLICATION_CONSULTATION: "Консультация",
    APPLICATION_AWAITING_DEPOSIT: "Ожидает предоплату",
    APPLICATION_BOOKED: "Записана",
    APPLICATION_COMPLETED: "Завершена",
    APPLICATION_CANCELED: "Отменена",
    APPLICATION_REJECTED: "Отклонена",
}
APPOINTMENT_STATUS_LABELS = {
    APPOINTMENT_DRAFT: "Черновик",
    APPOINTMENT_PENDING: "Ожидает подтверждения",
    APPOINTMENT_CONFIRMED: "Подтверждена",
    APPOINTMENT_COMPLETED: "Завершена",
    APPOINTMENT_CANCELED: "Отменена",
}
DEPOSIT_STATUS_LABELS = {
    DEPOSIT_NOT_REQUIRED: "Не требуется",
    DEPOSIT_PENDING: "Ожидается",
    DEPOSIT_PAID: "Получена",
    DEPOSIT_REFUNDED: "Возвращена",
}
COLOR_MODE_LABELS = {
    COLOR_BLACK_AND_GREY: "Чёрно-белая",
    COLOR_COLOR: "Цветная",
    COLOR_UNDECIDED: "Не определено",
}

templates.env.globals.update(
    application_status_labels=APPLICATION_STATUS_LABELS,
    appointment_status_labels=APPOINTMENT_STATUS_LABELS,
    deposit_status_labels=DEPOSIT_STATUS_LABELS,
    color_mode_labels=COLOR_MODE_LABELS,
)


@router.get("/applications", response_class=HTMLResponse, name="studio-applications")
async def applications_page(
    request: Request,
    session_token: Annotated[str, Depends(require_studio_session)],
    application_status: str = Query("active", alias="status"),
    search: str | None = Query(None, alias="q", max_length=200),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    items = await StudioBookingService(db).list_applications(
        status_filter=application_status,
        search=search,
    )
    return _template(
        request,
        "studio/applications.html",
        session_token,
        settings,
        items=items,
        current_status=application_status,
        application_filters=APPLICATION_FILTERS,
        search=search or "",
        studio_timezone=STUDIO_TIMEZONE,
    )


@router.get(
    "/applications/{application_id}",
    response_class=HTMLResponse,
    name="studio-application-detail",
)
async def application_detail(
    request: Request,
    application_id: uuid.UUID,
    session_token: Annotated[str, Depends(require_studio_session)],
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    try:
        detail = await StudioBookingService(db).get_application_detail(application_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found") from exc

    return _template(
        request,
        "studio/application_detail.html",
        session_token,
        settings,
        detail=detail,
        notice=request.query_params.get("notice"),
        error=request.query_params.get("error"),
        studio_timezone=STUDIO_TIMEZONE,
    )


@router.get("/appointments", response_class=HTMLResponse, name="studio-appointments")
async def appointments_page(
    request: Request,
    session_token: Annotated[str, Depends(require_studio_session)],
    view: str = Query("upcoming"),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    items = await StudioBookingService(db).list_appointments(view=view)
    return _template(
        request,
        "studio/appointments.html",
        session_token,
        settings,
        items=items,
        current_view=view,
        appointment_filters=APPOINTMENT_FILTERS,
        studio_timezone=STUDIO_TIMEZONE,
    )


@router.post(
    "/conversations/{conversation_id}/applications",
    dependencies=[Depends(require_studio_csrf)],
)
async def create_application(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        application = await StudioBookingService(db).create_application_from_conversation(
            conversation_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    return _application_redirect(application.id, "Заявка создана")


@router.post(
    "/applications/{application_id}/update",
    dependencies=[Depends(require_studio_csrf)],
)
async def update_application(
    application_id: uuid.UUID,
    idea: Annotated[str, Form()] = "",
    placement: Annotated[str, Form()] = "",
    size_details: Annotated[str, Form()] = "",
    color_mode: Annotated[str, Form()] = COLOR_UNDECIDED,
    style_preferences: Annotated[str, Form()] = "",
    budget_min: Annotated[str, Form()] = "",
    budget_max: Annotated[str, Form()] = "",
    desired_date: Annotated[str, Form()] = "",
    client_comment: Annotated[str, Form()] = "",
    internal_notes: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await StudioBookingService(db).update_application(
            application_id,
            idea=idea,
            placement=placement,
            size_details=size_details,
            color_mode=color_mode,
            style_preferences=style_preferences,
            budget_min=_optional_int(budget_min),
            budget_max=_optional_int(budget_max),
            desired_date=_optional_date(desired_date),
            client_comment=client_comment,
            internal_notes=internal_notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found") from exc
    except ValueError as exc:
        return _application_error_redirect(application_id, str(exc))
    return _application_redirect(application_id, "Заявка сохранена")


@router.post(
    "/applications/{application_id}/status/{target_status}",
    dependencies=[Depends(require_studio_csrf)],
)
async def transition_application(
    application_id: uuid.UUID,
    target_status: str,
    reason: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await StudioBookingService(db).transition_application(
            application_id,
            target_status,
            reason=reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found") from exc
    except (InvalidApplicationTransitionError, ValueError) as exc:
        return _application_error_redirect(application_id, str(exc))
    label = APPLICATION_STATUS_LABELS.get(target_status, target_status)
    return _application_redirect(application_id, f"Этап изменён: {label}")


@router.post(
    "/applications/{application_id}/references",
    dependencies=[Depends(require_studio_csrf)],
)
async def add_reference(
    application_id: uuid.UUID,
    reference_type: Annotated[str, Form()],
    value: Annotated[str, Form()],
    file_unique_id: Annotated[str, Form()] = "",
    file_name: Annotated[str, Form()] = "",
    caption: Annotated[str, Form()] = "",
    source_message_id: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await StudioBookingService(db).add_reference(
            application_id,
            reference_type=reference_type,
            value=value,
            file_unique_id=file_unique_id,
            file_name=file_name,
            caption=caption,
            source_message_id=source_message_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found") from exc
    except ValueError as exc:
        return _application_error_redirect(application_id, str(exc))
    return _application_redirect(application_id, "Референс добавлен")


@router.post(
    "/applications/{application_id}/appointment",
    dependencies=[Depends(require_studio_csrf)],
)
async def save_appointment(
    application_id: uuid.UUID,
    scheduled_start: Annotated[str, Form()],
    duration_minutes: Annotated[int, Form(ge=1, le=1440)],
    quoted_price: Annotated[str, Form()] = "",
    deposit_amount: Annotated[str, Form()] = "",
    appointment_status: Annotated[str, Form(alias="status")] = APPOINTMENT_DRAFT,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        parsed_start = _parse_local_datetime(scheduled_start)
        await StudioBookingService(db).save_appointment(
            application_id,
            scheduled_start=parsed_start,
            duration_minutes=duration_minutes,
            quoted_price=_optional_int(quoted_price),
            deposit_amount=_optional_int(deposit_amount),
            status=appointment_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found") from exc
    except (AppointmentConflictError, ValueError, InvalidApplicationTransitionError) as exc:
        return _application_error_redirect(application_id, str(exc))
    return _application_redirect(application_id, "Запись сохранена")


@router.post(
    "/appointments/{appointment_id}/status/{target_status}",
    dependencies=[Depends(require_studio_csrf)],
)
async def transition_appointment(
    appointment_id: uuid.UUID,
    target_status: str,
    application_id: Annotated[uuid.UUID, Form()],
    reason: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await StudioBookingService(db).transition_appointment(
            appointment_id,
            target_status,
            reason=reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Appointment not found") from exc
    except (AppointmentConflictError, ValueError, InvalidAppointmentTransitionError) as exc:
        return _application_error_redirect(application_id, str(exc))
    label = APPOINTMENT_STATUS_LABELS.get(target_status, target_status)
    return _application_redirect(application_id, f"Статус записи: {label}")


@router.post(
    "/appointments/{appointment_id}/deposit/{target_status}",
    dependencies=[Depends(require_studio_csrf)],
)
async def transition_deposit(
    appointment_id: uuid.UUID,
    target_status: str,
    application_id: Annotated[uuid.UUID, Form()],
    deposit_amount: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await StudioBookingService(db).transition_deposit(
            appointment_id,
            target_status,
            deposit_amount=_optional_int(deposit_amount),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Appointment not found") from exc
    except ValueError as exc:
        return _application_error_redirect(application_id, str(exc))
    label = DEPOSIT_STATUS_LABELS.get(target_status, target_status)
    return _application_redirect(application_id, f"Предоплата: {label}")


def _template(
    request: Request,
    name: str,
    session_token: str,
    settings: Settings,
    **context,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name=name,
        context={
            **context,
            "csrf_token": create_csrf_token(
                session_token,
                settings.admin_api_key.get_secret_value(),
            ),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _application_redirect(application_id: uuid.UUID, notice: str) -> RedirectResponse:
    return RedirectResponse(
        f"/studio/applications/{application_id}?notice={quote(notice)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _application_error_redirect(application_id: uuid.UUID, error: str) -> RedirectResponse:
    return RedirectResponse(
        f"/studio/applications/{application_id}?error={quote(error)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _optional_int(value: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValueError("Expected integer value") from exc


def _optional_date(value: str) -> date | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Invalid date") from exc


def _parse_local_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid appointment date and time") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=STUDIO_TIMEZONE)
    return parsed
