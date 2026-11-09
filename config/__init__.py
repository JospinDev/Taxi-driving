# Charge Celery au démarrage de Django pour que @shared_task utilise cette app.
from .celery import app as celery_app

__all__ = ('celery_app',)
