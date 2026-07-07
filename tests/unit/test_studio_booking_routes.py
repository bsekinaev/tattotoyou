"""HTTP-контракт продуктовых экранов записи."""

from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.studio import bookings as bookings_module
from app.api.studio.auth import STUDIO_SESSION_COOKIE, create_studio_session_token
from app.api.studio.bookings import router
from app.core.config import get_settings
from app.infrastructure.db.session import get_db_session

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


def test_applications_page_requires_studio_session() -> None:
    response = create_client().get("/studio/applications")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/studio/login?next=")


def test_appointments_page_requires_studio_session() -> None:
    response = create_client().get("/studio/appointments")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/studio/login?next=")


def test_product_templates_include_booking_workflow() -> None:
    detail = Path("src/app/templates/studio/application_detail.html").read_text(encoding="utf-8")
    base = Path("src/app/templates/studio/base.html").read_text(encoding="utf-8")

    assert "Параметры проекта" in detail
    assert "Предоплата получена" in detail
    assert "/studio/applications" in base
    assert "/studio/appointments" in base


def authenticated_client() -> TestClient:
    client = create_client()
    client.cookies.set(
        STUDIO_SESSION_COOKIE,
        create_studio_session_token(ADMIN_KEY),
    )
    return client


def test_applications_page_renders_for_authenticated_studio(
    monkeypatch,
) -> None:
    async def list_applications(self, **_kwargs):
        return []

    monkeypatch.setattr(
        bookings_module.StudioBookingService,
        "list_applications",
        list_applications,
    )
    client = authenticated_client()
    client.app.dependency_overrides[get_db_session] = lambda: None

    response = client.get("/studio/applications")

    assert response.status_code == 200
    assert "Заявки на тату" in response.text
    assert "Заявок по выбранному фильтру нет" in response.text


def test_appointments_page_renders_for_authenticated_studio(
    monkeypatch,
) -> None:
    async def list_appointments(self, **_kwargs):
        return []

    monkeypatch.setattr(
        bookings_module.StudioBookingService,
        "list_appointments",
        list_appointments,
    )
    client = authenticated_client()
    client.app.dependency_overrides[get_db_session] = lambda: None

    response = client.get("/studio/appointments")

    assert response.status_code == 200
    assert "Сеансы" in response.text
    assert "Записей по выбранному фильтру нет" in response.text


def test_booking_form_errors_redirect_back_to_application() -> None:
    import uuid

    application_id = uuid.uuid4()
    response = bookings_module._application_error_redirect(
        application_id,
        "Выбранное время пересекается с другой записью",
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/studio/applications/{application_id}?error=")
