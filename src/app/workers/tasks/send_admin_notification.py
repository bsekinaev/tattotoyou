import asyncio

from app.core.logging import get_logger
from app.services.notifications.admin_notifier import AdminNotifier
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    bind=True,
    name="send_admin_notification",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 5},
    retry_backoff=True,
)
def send_admin_notification_task(
    self,
    client_name: str,
    client_username: str | None,
    reason: str,
    last_message: str,
    chat_id: int,
):
    """
    Celery задача для отправки уведомления админу.
    """
    logger.info("admin_notification_task_started", reason=reason)
    try:
        asyncio.run(
            AdminNotifier.notify_escalation(
                client_name=client_name,
                client_username=client_username,
                reason=reason,
                last_message=last_message,
                chat_id=chat_id,
            )
        )
        logger.info("admin_notification_task_completed")
    except Exception as exc:
        logger.exception("admin_notification_task_failed", error=str(exc))
        raise exc
