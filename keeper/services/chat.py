"""
Module 9 — Chat course (client ↔ chauffeur).

Le fil s'ouvre dès qu'un chauffeur accepte la course et se ferme à la clôture :
l'historique reste lisible, mais on n'écrit plus dans une course terminée.

Diffusion : `ride.message` va au groupe `ride_<id>` (les deux parties y sont
quand elles ont le fil ouvert). Pour le destinataire qui n'a pas l'écran
ouvert, c'est la notification in-app/push qui fait le relais — on évite ainsi
d'envoyer deux fois le même message.
"""
import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from keeper.models import Ride, RideMessage
from keeper.services import notifications, realtime
from keeper.services.exceptions import BusinessError

logger = logging.getLogger(__name__)

# On peut écrire tant que la course est en cours avec un chauffeur assigné.
WRITABLE_STATUSES = Ride.DRIVER_ACTIVE_STATUSES


def get_ride_for_chat(ride_id, user):
    """La course si l'utilisateur en est le client ou le chauffeur assigné."""
    ride = (
        Ride.objects.select_related('client', 'driver__user')
        .filter(pk=ride_id)
        .first()
    )
    if ride is None:
        raise NotFound("Course introuvable.")
    if user.is_admin_role or ride.client_id == user.pk:
        return ride
    profile = getattr(user, 'driver_profile', None) if user.is_driver else None
    if profile is not None and ride.driver_id == profile.pk:
        return ride
    raise NotFound("Course introuvable.")


def is_participant(ride, user):
    """Client ou chauffeur assigné. Un admin peut lire le fil, pas y écrire."""
    if ride.client_id == user.pk:
        return True
    profile = getattr(user, 'driver_profile', None) if user.is_driver else None
    return profile is not None and ride.driver_id == profile.pk


def other_party(ride, user):
    """L'interlocuteur de `user` sur cette course (None si pas encore de chauffeur)."""
    if ride.client_id == user.pk:
        return ride.driver.user if ride.driver_id else None
    return ride.client


def thread(ride):
    return (
        RideMessage.objects.filter(ride=ride)
        .select_related('sender')
        .order_by('created_at')
    )


def unread_count(ride, user):
    return RideMessage.objects.filter(ride=ride, read_at__isnull=True).exclude(
        sender_id=user.pk,
    ).count()


def send_message(ride_id, sender, body):
    with transaction.atomic():
        ride = get_ride_for_chat(ride_id, sender)
        if not is_participant(ride, sender):
            raise PermissionDenied("Seuls le client et le chauffeur de la course peuvent écrire.")
        if ride.driver_id is None:
            raise BusinessError("Le chat s'ouvre dès qu'un chauffeur accepte la course.")
        if ride.status not in WRITABLE_STATUSES:
            raise BusinessError("Cette course est terminée : le chat est en lecture seule.")

        recipient = other_party(ride, sender)
        if recipient is None:
            raise BusinessError("Aucun interlocuteur sur cette course.")

        message = RideMessage.objects.create(ride=ride, sender=sender, body=body)

        payload = _payload(message, ride)
        transaction.on_commit(lambda: realtime.send_to_ride(ride.pk, 'ride.message', payload))

        # Relais hors écran : popup in-app + push FCM chez le destinataire.
        notifications.notify(
            recipient, 'ride_message', ride=ride,
            channels=(notifications.PUSH,),
            context={
                'sender_name': message.sender.get_full_name() or message.sender.username,
                'preview': body if len(body) <= 120 else f"{body[:117]}…",
            },
            data={'message_id': message.pk},
        )
    return message


def mark_thread_read(ride_id, user):
    """Marque comme lus les messages reçus, et prévient l'expéditeur (double coche)."""
    with transaction.atomic():
        ride = get_ride_for_chat(ride_id, user)
        if not is_participant(ride, user):
            return 0  # un admin qui consulte ne « lit » pas à la place du destinataire
        now = timezone.now()
        updated = RideMessage.objects.filter(
            ride=ride, read_at__isnull=True,
        ).exclude(sender_id=user.pk).update(read_at=now)

        if updated:
            transaction.on_commit(lambda: realtime.send_to_ride(
                ride.pk, 'ride.message_read',
                {'ride_id': ride.pk, 'reader_id': user.pk, 'read_at': now},
            ))
    return updated


def _payload(message, ride):
    sender = message.sender
    return {
        'id': message.pk,
        'ride_id': ride.pk,
        'sender': sender.pk,
        'sender_name': sender.get_full_name() or sender.username,
        'sender_role': sender.role,
        'body': message.body,
        'read_at': None,
        'created_at': message.created_at,
    }
