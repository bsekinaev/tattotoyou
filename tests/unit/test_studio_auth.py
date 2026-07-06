"""Тесты подписанной сессии web-панели Сони."""

from app.api.studio.auth import (
    create_csrf_token,
    create_studio_session_token,
    validate_csrf_token,
    validate_studio_session_token,
)

ADMIN_KEY = "a" * 32


def test_studio_session_token_is_signed_and_expires() -> None:
    token = create_studio_session_token(ADMIN_KEY, issued_at=1_000)

    assert validate_studio_session_token(token, ADMIN_KEY, now=1_100)
    assert not validate_studio_session_token(token, ADMIN_KEY, now=50_000)
    assert not validate_studio_session_token(token, "b" * 32, now=1_100)


def test_studio_session_rejects_tampering() -> None:
    token = create_studio_session_token(ADMIN_KEY, issued_at=1_000)
    timestamp, signature = token.split(".", 1)

    assert not validate_studio_session_token(
        f"{int(timestamp) + 1}.{signature}",
        ADMIN_KEY,
        now=1_100,
    )


def test_csrf_token_is_bound_to_session() -> None:
    first_session = create_studio_session_token(ADMIN_KEY, issued_at=1_000)
    second_session = create_studio_session_token(ADMIN_KEY, issued_at=1_001)
    token = create_csrf_token(first_session, ADMIN_KEY)

    assert validate_csrf_token(token, first_session, ADMIN_KEY)
    assert not validate_csrf_token(token, second_session, ADMIN_KEY)
