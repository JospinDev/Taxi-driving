from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from keeper.models import Notification
from keeper.services.notifications import NotificationRetry, deliver, enqueue


@shared_task(bind=True, acks_late=True, max_retries=5)
def send_notification(self, notification_id):
    try:
        deliver(notification_id)
    except NotificationRetry as exc:
        # Backoff exponentiel : 10s, 20s, 40s… (max 5 min)
        raise self.retry(exc=exc, countdown=min(300, 10 * 2 ** self.request.retries))


@shared_task
def retry_pending_notifications(limit=500):
    """Filet de sécurité (Celery beat) : relance les notifications bloquées."""
    now = timezone.now()
    # Worker mort pendant l'envoi → on remet en file
    Notification.objects.filter(
        status=Notification.Status.SENDING, created_at__lt=now - timedelta(minutes=10),
    ).update(status=Notification.Status.PENDING)

    ids = list(
        Notification.objects.filter(
            status=Notification.Status.PENDING, created_at__lt=now - timedelta(minutes=2),
        ).order_by('created_at').values_list('pk', flat=True)[:limit]
    )
    enqueue(ids)
    return len(ids)
