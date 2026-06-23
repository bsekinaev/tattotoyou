from app.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()

    print(f"✅ App: {settings.app_name} v{settings.app_version}")
    print(f"✅ Debug: {settings.debug}")
    print(f"✅ Log level: {settings.log_level}")
    print(f"✅ Postgres DSN: {settings.postgres_dsn}")
    print(f"✅ Redis URL: {settings.redis_url}")
    print(f"✅ Telegram bot token: {settings.telegram_bot_token}")
