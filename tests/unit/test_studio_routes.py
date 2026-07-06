"""HTTP-контракт входа в панель Сони."""

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.studio.auth import STUDIO_SESSION_COOKIE
from app.api.studio.dashboard import router
from app.core.config import get_settings

ADMIN_KEY = "a" * 32


def create_client() -> TestClient:
    app = FastAPI()
    app.mount(
        "/studio/static",
        StaticFiles(directory=Path("src/app/static")),
        name="studio-static",
    )
    app.include_router(router, prefix="/studio")
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        admin_api_key=SecretStr(ADMIN_KEY),
        debug=True,
    )
    return TestClient(app, follow_redirects=False)


def test_login_page_is_available_without_session() -> None:
    response = create_client().get("/studio/login")

    assert response.status_code == 200
    assert "Вход для Сони" in response.text


def test_invalid_login_does_not_create_session() -> None:
    response = create_client().post(
        "/studio/login",
        data={"admin_key": "wrong", "next_path": "/studio/conversations"},
    )

    assert response.status_code == 401
    assert STUDIO_SESSION_COOKIE not in response.cookies


def test_valid_login_creates_http_only_session() -> None:
    response = create_client().post(
        "/studio/login",
        data={"admin_key": ADMIN_KEY, "next_path": "/studio/conversations"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/studio/conversations"
    assert STUDIO_SESSION_COOKIE in response.cookies
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]


def test_login_rejects_external_next_url() -> None:
    response = create_client().post(
        "/studio/login",
        data={"admin_key": ADMIN_KEY, "next_path": "https://attacker.invalid"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/studio/conversations"


def test_protected_page_redirects_to_login_without_session() -> None:
    response = create_client().get("/studio/conversations?status=escalated")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/studio/login?next=")
