"""Проверки централизованного удаления секретов и PII из логов."""

from app.core.logging import redact_sensitive_data, sanitize_log_text


def test_redaction_processor_removes_secret_and_pii_fields() -> None:
    event = {
        "event": "request_processed",
        "request_id": "req-123",
        "update_id": 456,
        "chat_id": "987654321",
        "user_id": "123456789",
        "text_snippet": "Мой номер +7 999 123-45-67",
        "telegram_bot_token": "super-secret-token",
        "nested": {
            "email": "client@example.com",
            "status": "failed",
        },
    }

    result = redact_sensitive_data(None, "info", event)  # type: ignore[arg-type]

    assert result["event"] == "request_processed"
    assert result["request_id"] == "req-123"
    assert result["update_id"] == 456
    assert result["chat_id"] == "[REDACTED]"
    assert result["user_id"] == "[REDACTED]"
    assert result["text_snippet"] == "[REDACTED]"
    assert result["telegram_bot_token"] == "[REDACTED]"
    assert result["nested"] == {
        "email": "[REDACTED]",
        "status": "failed",
    }


def test_sanitize_log_text_removes_tokens_credentials_and_contacts() -> None:
    raw = (
        "POST https://api.telegram.org/bot123456:ABC-secret/sendMessage "
        "postgresql://tattoo:db-password@postgres/tattoo "
        "Authorization: Bearer access-token-123 "
        "client@example.com +7 (999) 123-45-67"
    )

    sanitized = sanitize_log_text(raw)

    assert "123456:ABC-secret" not in sanitized
    assert "db-password" not in sanitized
    assert "access-token-123" not in sanitized
    assert "client@example.com" not in sanitized
    assert "+7 (999) 123-45-67" not in sanitized
    assert sanitized.count("[REDACTED]") >= 5
