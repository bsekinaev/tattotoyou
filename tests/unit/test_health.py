"""Регрессионные тесты liveness/readiness endpoints."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app import main as main_module


class AsyncConnectionContext:
    """Минимальный async context manager для подмены SQLAlchemy connection."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def __aenter__(self) -> Any:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_liveness_does_not_depend_on_database_or_redis() -> None:
    client = TestClient(main_module.create_app())

    response = client.get("/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "checks" not in response.json()
    assert response.headers["X-Request-ID"]


def test_readiness_returns_only_generic_dependency_errors(monkeypatch) -> None:
    database_error = RuntimeError(
        "postgresql://tattoo:secret-password@localhost/tattoo client@example.com"
    )
    engine = MagicMock()
    engine.connect.return_value = AsyncConnectionContext(
        AsyncMock(execute=AsyncMock(side_effect=database_error))
    )
    redis = AsyncMock()
    redis.ping.side_effect = RuntimeError("redis://:secret-password@localhost")

    monkeypatch.setattr(main_module, "async_engine", engine)
    monkeypatch.setattr(main_module, "redis_client", redis)
    client = TestClient(main_module.create_app())

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "version": main_module.get_settings().app_version,
        "checks": {"database": "error", "redis": "error"},
    }
    assert "secret-password" not in response.text
    assert "client@example.com" not in response.text


def test_readiness_and_legacy_health_are_successful(monkeypatch) -> None:
    connection = AsyncMock()
    connection.execute = AsyncMock(return_value=None)
    engine = MagicMock()
    engine.connect.return_value = AsyncConnectionContext(connection)
    redis = AsyncMock()
    redis.ping.return_value = True

    monkeypatch.setattr(main_module, "async_engine", engine)
    monkeypatch.setattr(main_module, "redis_client", redis)
    client = TestClient(main_module.create_app())

    ready_response = client.get("/ready")
    health_response = client.get("/health")

    assert ready_response.status_code == 200
    assert ready_response.json()["status"] == "ok"
    assert ready_response.json()["checks"] == {"database": "ok", "redis": "ok"}
    assert health_response.status_code == 200
    assert health_response.json() == ready_response.json()


def test_global_exception_response_hides_exception_details() -> None:
    app = main_module.create_app()

    @app.get("/test-unhandled-error")
    async def raise_unhandled_error() -> None:
        raise RuntimeError(
            "postgresql://tattoo:secret-password@localhost/tattoo client@example.com"
        )

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/test-unhandled-error")

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "secret-password" not in response.text
    assert "client@example.com" not in response.text
