from django.db import models

from keeper.models.accounts import User
from keeper.models.rides import Ride


class RideMessage(models.Model):
    """
    Message échangé entre le client et le chauffeur d'une course.

    Le fil est lié à la course : il s'ouvre quand un chauffeur accepte et se
    ferme quand la course se termine (l'historique reste consultable).
    """

    id = models.BigAutoField(primary_key=True)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ride_messages')
    body = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(
        null=True, blank=True, help_text="Horodatage de lecture par le destinataire.",
    )

    class Meta:
        db_table = 'rides_message'
        verbose_name = 'Message de course'
        verbose_name_plural = 'Messages de course'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['ride', 'created_at']),
            # Compteur de non-lus : filtré sur (course, expéditeur, read_at)
            models.Index(fields=['ride', 'sender', 'read_at']),
        ]

    def __str__(self):
        return f"Message ride#{self.ride_id} de user#{self.sender_id}"
