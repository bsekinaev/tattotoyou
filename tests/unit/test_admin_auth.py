"""Regression tests for administration API authentication."""

from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.admin.auth import ADMIN_API_KEY_HEADER, require_admin
from app.core.config import get_settings

ADMIN_KEY = "a" * 32


def create_test_client() -> TestClient:
    """Create an isolated FastAPI app protected by the real dependency."""
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        admin_api_key=SecretStr(ADMIN_KEY)
    )

    @app.get("/admin-check", dependencies=[Depends(require_admin)])
    async def admin_check() -> dict[str, bool]:
        return {"authenticated": True}

    return TestClient(app)


def test_admin_endpoint_rejects_missing_key() -> None:
    client = create_test_client()

    response = client.get("/admin-check")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid admin credentials"}
    assert response.headers["www-authenticate"] == "ApiKey"


def test_admin_endpoint_rejects_invalid_key() -> None:
    client = create_test_client()

    response = client.get(
        "/admin-check",
        headers={ADMIN_API_KEY_HEADER: "b" * 32},
    )

    assert response.status_code == 401


def test_admin_endpoint_accepts_configured_key() -> None:
    client = create_test_client()

    response = client.get(
        "/admin-check",
        headers={ADMIN_API_KEY_HEADER: ADMIN_KEY},
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}