"""Уведомления администратору о событиях, требующих ручного ответа."""

from html import escape

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.platforms.telegram.client import TelegramClient

logger = get_logger(__name__)
settings = get_settings()


class AdminNotifier:
    """Отправляет уведомления администратору при эскалациях."""

    @classmethod
    async def notify_escalation(
        cls,
        client_name: str,
        client_username: str | None,
        reason: str,
        last_message: str,
        chat_id: int,
    ) -> None:
        """Сформировать безопасное HTML-уведомление об эскалации."""
        tg_client = TelegramClient()
        try:
            username_text = f"@{client_username}" if client_username else "без username"

            safe_name = escape(client_name)
            safe_username = escape(username_text)
            safe_reason = escape(reason)
            safe_message = escape(last_message[:200])

            message = (
                f"🚨 <b>ЭСКАЛАЦИЯ</b>\n\n"
                f"👤 Клиент: {safe_name} ({safe_username})\n"
                f"📍 Причина: <code>{safe_reason}</code>\n"
                f"💬 Последнее сообщение:\n<i>{safe_message}</i>\n\n"
                f"🔗 Chat ID: <code>{chat_id}</code>\n"
                f"⚡ Требуется твой ответ!"
            )

            await tg_client.send_message(
                chat_id=settings.telegram_admin_chat_id,
                text=message,
                parse_mode="HTML",
            )
            logger.info(
                "admin_notification_sent",
                reason=reason,
            )
        except Exception:
            logger.exception("admin_notification_failed")
            raise
        finally:
            await tg_client.close()