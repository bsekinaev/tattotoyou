"""HTML-панель управления диалогами и клиентами."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.api.admin.auth import is_valid_admin_api_key
from app.api.studio.auth import (
    STUDIO_SESSION_COOKIE,
    STUDIO_SESSION_MAX_AGE_SECONDS,
    create_csrf_token,
    create_studio_session_token,
    require_studio_csrf,
    require_studio_session,
    validate_studio_session_token,
)
from app.api.studio.bookings import APPLICATION_STATUS_LABELS
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.infrastructure.db.repositories.conversation_repository import (
    InvalidConversationTransitionError,
)
from app.infrastructure.db.session import get_db_session
from app.services.studio_dashboard import (
    CONVERSATION_FILTERS,
    LEAD_STATUSES,
    StudioDashboardService,
)

logger = get_logger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))

STATUS_LABELS = {
    "active": "AI отвечает",
    "escalated": "Нужна Соня",
    "human_owned": "Соня отвечает",
    "closed": "Закрыт",
    "spam": "Спам",
}
LEAD_STATUS_LABELS = {
    "new": "Новый",
    "qualification": "Уточнение задачи",
    "consultation": "Консультация",
    "waiting_payment": "Ожидает предоплату",
    "booked": "Записан",
    "completed": "Завершён",
    "lost": "Не состоялся",
}

templates.env.globals.update(
    status_labels=STATUS_LABELS,
    lead_status_labels=LEAD_STATUS_LABELS,
    lead_statuses=LEAD_STATUSES,
    application_status_labels=APPLICATION_STATUS_LABELS,
)


@router.get("/login", response_class=HTMLResponse, name="studio-login")
async def login_page(
    request: Request,
    next_path: str = Query("/studio/conversations", alias="next"),
    session_token: Annotated[str | None, Cookie(alias=STUDIO_SESSION_COOKIE)] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> Response:
    admin_key = settings.admin_api_key.get_secret_value()
    safe_next = _safe_next_path(next_path)
    if validate_studio_session_token(session_token, admin_key):
        return RedirectResponse(safe_next, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="studio/login.html",
        context={"next_path": safe_next, "error": None},
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    admin_key: Annotated[str, Form()],
    next_path: Annotated[str, Form()] = "/studio/conversations",
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> Response:
    expected_key = settings.admin_api_key.get_secret_value()
    safe_next = _safe_next_path(next_path)
    if not is_valid_admin_api_key(admin_key, expected_key):
        logger.warning("studio_login_rejected")
        return templates.TemplateResponse(
            request=request,
            name="studio/login.html",
            context={"next_path": safe_next, "error": "Неверный ключ доступа"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    token = create_studio_session_token(expected_key)
    response = RedirectResponse(safe_next, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=STUDIO_SESSION_COOKIE,
        value=token,
        max_age=STUDIO_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=not settings.debug,
        samesite="strict",
        path="/studio",
    )
    logger.info("studio_login_succeeded")
    return response


@router.post("/logout", dependencies=[Depends(require_studio_csrf)])
async def logout() -> RedirectResponse:
    response = RedirectResponse("/studio/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(STUDIO_SESSION_COOKIE, path="/studio")
    return response


@router.get("/", include_in_schema=False)
async def studio_root(
    _session: Annotated[str, Depends(require_studio_session)],
) -> RedirectResponse:
    return RedirectResponse("/studio/conversations", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/conversations", response_class=HTMLResponse, name="studio-conversations")
async def conversations_page(
    request: Request,
    session_token: Annotated[str, Depends(require_studio_session)],
    conversation_status: str = Query("open", alias="status"),
    search: str | None = Query(None, alias="q", max_length=200),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    items = await StudioDashboardService(db).list_conversations(
        status_filter=conversation_status,
        search=search,
    )
    return _template(
        request,
        "studio/conversations.html",
        session_token,
        settings,
        items=items,
        current_status=conversation_status,
        conversation_filters=CONVERSATION_FILTERS,
        search=search or "",
    )


@router.get(
    "/fragments/conversations",
    response_class=HTMLResponse,
    name="studio-conversations-fragment",
)
async def conversations_fragment(
    request: Request,
    session_token: Annotated[str, Depends(require_studio_session)],
    conversation_status: str = Query("open", alias="status"),
    search: str | None = Query(None, alias="q", max_length=200),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    items = await StudioDashboardService(db).list_conversations(
        status_filter=conversation_status,
        search=search,
    )
    return _template(
        request,
        "studio/_conversation_list.html",
        session_token,
        settings,
        items=items,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_class=HTMLResponse,
    name="studio-conversation-detail",
)
async def conversation_detail(
    request: Request,
    conversation_id: uuid.UUID,
    session_token: Annotated[str, Depends(require_studio_session)],
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    try:
        detail = await StudioDashboardService(db).get_conversation_detail(conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc

    return _template(
        request,
        "studio/conversation_detail.html",
        session_token,
        settings,
        detail=detail,
        notice=request.query_params.get("notice"),
    )


@router.post(
    "/conversations/{conversation_id}/actions/{action}",
    dependencies=[Depends(require_studio_csrf)],
)
async def conversation_action(
    conversation_id: uuid.UUID,
    action: str,
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await StudioDashboardService(db).transition(conversation_id, action)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except InvalidConversationTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    labels = {
        "takeover": "Диалог взят Соней",
        "return_to_ai": "Диалог возвращён AI",
        "close": "Диалог закрыт",
    }
    return _detail_redirect(conversation_id, labels.get(action, "Состояние обновлено"))


@router.post(
    "/conversations/{conversation_id}/reply",
    dependencies=[Depends(require_studio_csrf)],
)
async def send_reply(
    conversation_id: uuid.UUID,
    content: Annotated[str, Form(min_length=1, max_length=4000)],
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        await StudioDashboardService(db).send_human_reply(conversation_id, content)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc
    except (ValueError, InvalidConversationTransitionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail_redirect(conversation_id, "Ответ поставлен в очередь отправки")


@router.post(
    "/conversations/{conversation_id}/client",
    dependencies=[Depends(require_studio_csrf)],
)
async def update_client(
    conversation_id: uuid.UUID,
    lead_status: Annotated[str, Form()],
    internal_notes: Annotated[str, Form()] = "",
    tattoo_idea: Annotated[str, Form()] = "",
    placement: Annotated[str, Form()] = "",
    size_details: Annotated[str, Form()] = "",
    style_preferences: Annotated[str, Form()] = "",
    budget_details: Annotated[str, Form()] = "",
    desired_date: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    parsed_date: date | None = None
    if desired_date.strip():
        try:
            parsed_date = date.fromisoformat(desired_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid desired date") from exc

    try:
        await StudioDashboardService(db).update_client_profile(
            conversation_id,
            lead_status=lead_status,
            internal_notes=internal_notes,
            tattoo_idea=tattoo_idea,
            placement=placement,
            size_details=size_details,
            style_preferences=style_preferences,
            budget_details=budget_details,
            desired_date=parsed_date,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Client not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _detail_redirect(conversation_id, "Карточка клиента сохранена")


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


def _detail_redirect(conversation_id: uuid.UUID, notice: str) -> RedirectResponse:
    return RedirectResponse(
        f"/studio/conversations/{conversation_id}?notice={quote(notice)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _safe_next_path(value: str) -> str:
    if value.startswith("/studio/") and not value.startswith("//"):
        return value
    return "/studio/conversations"
