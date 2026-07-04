from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "tattoo_assistant",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.workers.tasks.process_telegram_update",
        "app.workers.tasks.deliver_outbound_message",
        "app.workers.tasks.send_admin_notification",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "recover-incoming-events": {
            "task": "recover_incoming_events",
            "schedule": float(settings.incoming_event_recovery_interval_seconds),
        },
        "recover-outbound-deliveries": {
            "task": "recover_outbound_deliveries",
            "schedule": float(settings.outbound_delivery_recovery_interval_seconds),
        },
    },
)
