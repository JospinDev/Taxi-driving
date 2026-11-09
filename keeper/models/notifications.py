from django.db import models

from keeper.models.accounts import User
from keeper.models.rides import Ride


class Notification(models.Model):
    """Journal des notifications envoyées (push FCM / WhatsApp)."""

    class Channel(models.TextChoices):
        IN_APP = 'in_app', "Dans l'application"
        PUSH = 'push', 'Push (FCM)'
        WHATSAPP = 'whatsapp', 'WhatsApp'

    class Status(models.TextChoices):
        PENDING = 'pending', 'En attente'
        SENDING = 'sending', "En cours d'envoi"
        SENT = 'sent', 'Envoyée'
        FAILED = 'failed', 'Échec'
        SKIPPED = 'skipped', 'Ignorée (non configuré / pas de destinataire)'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    ride = models.ForeignKey(
        Ride, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='notifications',
    )
    channel = models.CharField(max_length=10, choices=Channel.choices)
    event = models.CharField(max_length=50, db_index=True)
    title = models.CharField(max_length=150, blank=True)
    body = models.TextField()
    data = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    # Lecture : n'a de sens que pour le canal IN_APP (boîte de réception de l'app)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications_notification'
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', 'created_at']),
            # Compteur de non-lues (badge) : index partiel, très petit
            models.Index(
                fields=['user'], condition=models.Q(channel='in_app', is_read=False),
                name='notif_unread_in_app_idx',
            ),
        ]

    def __str__(self):
        return f"{self.channel}:{self.event} → {self.user_id} [{self.status}]"
