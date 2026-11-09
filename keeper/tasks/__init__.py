# Celery autodiscover importe keeper.tasks : on expose ici toutes les tâches.
from .dispatch import dispatch_ride  # noqa: F401
from .notifications import retry_pending_notifications, send_notification  # noqa: F401
