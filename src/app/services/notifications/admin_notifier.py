from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.platforms.telegram.client import TelegramClient

logger = get_logger(__name__)
settings = get_settings()


class AdminNotifier:
    """
    Отправляет уведомления администратору (Софии) при эскалациях.
    """

    @classmethod
    async def notify_escalation(
            cls,
            client_name: str,
            client_username: str | None,
            reason: str,
            last_message: str,
            chat_id: int,
    ) -> None:
        """
        Формирует и отправляет уведомление об эскалации.
        """
        tg_client = TelegramClient()
        try:
            # Формируем красивое сообщение для Софии
            username_text = f"@{client_username}" if client_username else "без username"

            message = (
                f"🚨 <b>ЭСКАЛАЦИЯ</b>\n\n"
                f"👤 Клиент: {client_name} ({username_text})\n"
                f"📍 Причина: <code>{reason}</code>\n"
                f"💬 Последнее сообщение:\n<i>{last_message[:200]}</i>\n\n"
                f"🔗 Chat ID: <code>{chat_id}</code>\n"
                f"⚡ Требуется твой ответ!"
            )

            await tg_client.send_message(
                chat_id=settings.telegram_admin_chat_id,
                text=message,
            )
            logger.info(
                "admin_notification_sent",
                reason=reason,
                client_name=client_name,
            )
        except Exception as e:
            logger.exception("admin_notification_failed", error=str(e))
            raise
        finally:
            await tg_client.close()