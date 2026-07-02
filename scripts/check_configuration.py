"""Проверить конфигурацию приложения без вывода секретов и полных DSN."""

from __future__ import annotations

from pydantic import ValidationError

from app.core.config import get_settings


def _configured(secret: object | None) -> str:
    return "configured" if secret is not None else "missing"


def main() -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        missing = sorted(
            str(error["loc"][0])
            for error in exc.errors()
            if error.get("type") == "missing" and error.get("loc")
        )
        print("Configuration: INVALID")
        if missing:
            print("Missing required variables: " + ", ".join(missing))
        else:
            print("One or more configuration values are invalid")
        return 1

    print("Configuration: OK")
    print(f"Application: {settings.app_name} {settings.app_version}")
    print(
        "PostgreSQL target: "
        f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )
    print(f"Redis target: {settings.redis_host}:{settings.redis_port}/{settings.redis_db}")
    print(f"Telegram bot token: {_configured(settings.telegram_bot_token)}")
    print(f"Telegram webhook secret: {_configured(settings.telegram_webhook_secret)}")
    print(f"GigaChat client credentials: {_configured(settings.gigachat_client_secret)}")
    print(f"Admin API key: {_configured(settings.admin_api_key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
