"""Подписанная cookie-сессия и CSRF-защита панели Сони."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Annotated
from urllib.parse import quote

from fastapi import Cookie, Depends, Form, HTTPException, Request, status

from app.core.config import Settings, get_settings

STUDIO_SESSION_COOKIE = "tattutuy_studio_session"
STUDIO_SESSION_MAX_AGE_SECONDS = 12 * 60 * 60
STUDIO_LOGIN_PATH = "/studio/login"


def create_studio_session_token(admin_key: str, *, issued_at: int | None = None) -> str:
    """Создать короткоживущий HMAC-токен без хранения серверной сессии."""
    timestamp = issued_at if issued_at is not None else int(time.time())
    payload = str(timestamp)
    signature = _signature(admin_key, f"studio-session:{payload}")
    return f"{payload}.{signature}"


def validate_studio_session_token(
    token: str | None,
    admin_key: str,
    *,
    now: int | None = None,
    max_age_seconds: int = STUDIO_SESSION_MAX_AGE_SECONDS,
) -> bool:
    """Проверить подпись и срок действия studio-сессии."""
    if not token:
        return False
    try:
        timestamp_text, provided_signature = token.split(".", 1)
        issued_at = int(timestamp_text)
    except (TypeError, ValueError):
        return False

    current_time = now if now is not None else int(time.time())
    age = current_time - issued_at
    if age < -60 or age > max_age_seconds:
        return False

    expected_signature = _signature(admin_key, f"studio-session:{timestamp_text}")
    return hmac.compare_digest(provided_signature, expected_signature)


def create_csrf_token(session_token: str, admin_key: str) -> str:
    """Привязать CSRF-токен к конкретной подписанной session cookie."""
    return _signature(admin_key, f"studio-csrf:{session_token}")


def validate_csrf_token(token: str | None, session_token: str, admin_key: str) -> bool:
    if not token:
        return False
    expected = create_csrf_token(session_token, admin_key)
    return hmac.compare_digest(token, expected)


async def require_studio_session(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias=STUDIO_SESSION_COOKIE)] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> str:
    """Разрешить страницу только владельцу действующей studio-сессии."""
    admin_key = settings.admin_api_key.get_secret_value()
    if validate_studio_session_token(session_token, admin_key):
        return str(session_token)

    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": f"{STUDIO_LOGIN_PATH}?next={quote(next_path, safe='')}"},
    )


async def require_studio_csrf(
    csrf_token: Annotated[str, Form()],
    session_token: Annotated[str, Depends(require_studio_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Проверить CSRF для всех изменяющих HTML-форм."""
    admin_key = settings.admin_api_key.get_secret_value()
    if validate_csrf_token(csrf_token, session_token, admin_key):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def _signature(admin_key: str, payload: str) -> str:
    return hmac.new(
        admin_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
