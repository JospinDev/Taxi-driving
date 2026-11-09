"""
Module 8 — Notifications (FCM & WhatsApp).

Principe :
1. notify() crée les lignes Notification DANS la transaction métier.
2. L'envoi réel est programmé via transaction.on_commit() → Celery.
   → si la transaction échoue, aucun message n'est envoyé.
3. deliver() (appelé par le worker) « réserve » la notification par un UPDATE
   conditionnel (PENDING → SENDING) : deux workers ne peuvent pas envoyer le
   même message, sans garder de verrou SQL pendant l'appel HTTP.
"""
import logging
import re

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from keeper.models import Notification, User
from keeper.services import realtime

logger = logging.getLogger(__name__)

IN_APP = Notification.Channel.IN_APP
PUSH = Notification.Channel.PUSH
WHATSAPP = Notification.Channel.WHATSAPP
MAX_ATTEMPTS = 5

# Comportement du popup côté app mobile, par événement.
#   ttl_s    : durée d'affichage avant disparition automatique
#   priority : 'high' = popup bloquant / son (ex: offre de course), 'normal' = toast
POPUP = {
    'ride_offer': {'ttl_s': settings.DISPATCH['OFFER_TIMEOUT_S'], 'priority': 'high'},
    'driver_assigned': {'ttl_s': 8, 'priority': 'high'},
    'driver_arrived': {'ttl_s': 10, 'priority': 'high'},
    'ride_cancelled': {'ttl_s': 8, 'priority': 'high'},
    'driver_cancelled': {'ttl_s': 8, 'priority': 'high'},
    'no_driver': {'ttl_s': 8, 'priority': 'high'},
    'ride_message': {'ttl_s': 6, 'priority': 'normal'},
}
DEFAULT_POPUP = {'ttl_s': 5, 'priority': 'normal'}


# ============================================================
# CATALOGUE DES MESSAGES
# ============================================================
MESSAGES = {
    # ---- Chauffeur ----
    'ride_offer': {
        'title': "Nouvelle course",
        'body': "Prise en charge à {pickup_distance_km} km : {pickup_address}. "
                "Estimation {estimated_fare} {currency}.",
    },
    'ride_cancelled': {
        'title': "Course annulée",
        'body': "La course #{ride_id} a été annulée. {reason}",
    },
    'payment_confirmed': {
        'title': "Paiement confirmé",
        'body': "Course #{ride_id} : {driver_earning} {currency} crédités sur votre portefeuille.",
    },
    'wallet_payout': {
        'title': "Versement effectué",
        'body': "Un versement de {amount} {currency} vous a été envoyé. Réf : {reference}",
    },
    # ---- Client ----
    'driver_assigned': {
        'title': "Chauffeur trouvé",
        'body': "Bonjour {client_name}, votre chauffeur {driver_name} arrive. "
                "Véhicule : {vehicle} ({color}), plaque {plate}. Tél : {driver_phone}.",
    },
    'driver_arrived': {
        'title': "Votre chauffeur est arrivé",
        'body': "{driver_name} vous attend au point de prise en charge "
                "(véhicule {vehicle}, plaque {plate}).",
    },
    'ride_started': {
        'title': "Course démarrée",
        'body': "Votre course #{ride_id} a démarré. Bon voyage !",
    },
    'ride_invoice': {
        'title': "Facture course #{ride_id}",
        'body': "Distance : {distance_km} km\n"
                "Embouteillage : {traffic_minutes} min\n"
                "Prise en charge : {fare_base} {currency}\n"
                "Kilomètres : {fare_km} {currency}\n"
                "Trafic : {fare_traffic} {currency}\n"
                "TOTAL : {fare_total} {currency}\n"
                "Paiement : {payment_method}\n"
                "Merci d'avoir voyagé avec Keeper !",
    },
    'driver_cancelled': {
        'title': "Changement de chauffeur",
        'body': "Votre chauffeur a annulé. Nous recherchons un autre chauffeur pour vous.",
    },
    'no_driver': {
        'title': "Aucun chauffeur disponible",
        'body': "Désolé, aucun chauffeur n'est disponible pour le moment. Réessayez dans quelques minutes.",
    },
    # ---- Commun (client ↔ chauffeur) ----
    'ride_message': {
        'title': "Message de {sender_name}",
        'body': "{preview}",
    },
}


class _SafeDict(dict):
    def __missing__(self, key):
        return ''


# ============================================================
# CRÉATION (dans la transaction métier)
# ============================================================
def notify(user, event, *, channels=(PUSH,), ride=None, context=None, data=None, in_app=True):
    """
    Enregistre les notifications et programme leur envoi après commit.
    - IN_APP (par défaut) : stockée comme « envoyée », poussée en WebSocket
      (popup si l'utilisateur est en ligne) et visible dans sa boîte de réception.
    - PUSH / WHATSAPP : envoyées par Celery.
    """
    template = MESSAGES[event]
    ctx = _SafeDict(currency=settings.CURRENCY, ride_id=getattr(ride, 'pk', ''))
    ctx.update(context or {})
    title = template['title'].format_map(ctx)
    body = template['body'].format_map(ctx).strip()

    payload = {'event': event}
    if ride is not None:
        payload['ride_id'] = ride.pk
    payload.update(data or {})

    now = timezone.now()
    all_channels = ([IN_APP] if in_app else []) + [c for c in channels if c != IN_APP]
    notifications = Notification.objects.bulk_create([
        Notification(
            user=user, ride=ride, channel=channel, event=event,
            title=title, body=body, data=payload,
            # In-app : rien à transmettre à un fournisseur externe
            status=Notification.Status.SENT if channel == IN_APP else Notification.Status.PENDING,
            sent_at=now if channel == IN_APP else None,
        )
        for channel in all_channels
    ])

    external_ids = [n.pk for n in notifications if n.channel != IN_APP]
    in_app_notif = next((n for n in notifications if n.channel == IN_APP), None)

    def after_commit():
        if in_app_notif is not None:
            push_in_app(in_app_notif)
        if external_ids:
            enqueue(external_ids)

    transaction.on_commit(after_commit)
    return notifications


def in_app_payload(notification):
    return {
        'id': notification.pk,
        'event': notification.event,
        'title': notification.title,
        'body': notification.body,
        'data': notification.data,
        'ride_id': notification.ride_id,
        'is_read': notification.is_read,
        'created_at': notification.created_at,
        'popup': POPUP.get(notification.event, DEFAULT_POPUP),
    }


def unread_count(user_id):
    return Notification.objects.filter(
        user_id=user_id, channel=IN_APP, is_read=False,
    ).count()


def push_in_app(notification):
    """WebSocket → groupe user_<id>. Si l'app est hors ligne, rien n'est perdu :
    la notification est déjà dans GET /notifications/."""
    realtime.send_to_user(notification.user_id, 'notification', {
        **in_app_payload(notification),
        'unread_count': unread_count(notification.user_id),
    })


def enqueue(notification_ids):
    from keeper.tasks.notifications import send_notification
    for nid in notification_ids:
        try:
            send_notification.delay(nid)
        except Exception:
            # Broker indisponible : la tâche périodique retry_pending la reprendra
            logger.exception("Impossible de programmer la notification %s", nid)


# ============================================================
# ENVOI (côté worker Celery)
# ============================================================
class NotificationSkipped(Exception):
    pass


class NotificationFailed(Exception):
    pass


class NotificationRetry(Exception):
    pass


def deliver(notification_id):
    """Envoie une notification. Lève NotificationRetry si l'erreur est temporaire."""
    claimed = Notification.objects.filter(
        pk=notification_id, status=Notification.Status.PENDING,
    ).update(status=Notification.Status.SENDING, attempts=F('attempts') + 1)
    if not claimed:
        return  # déjà envoyée / prise par un autre worker

    notification = Notification.objects.select_related('user').get(pk=notification_id)
    sender = _send_push if notification.channel == PUSH else _send_whatsapp

    try:
        message_id = sender(notification)
    except NotificationSkipped as exc:
        _finish(notification, Notification.Status.SKIPPED, error=str(exc))
    except NotificationFailed as exc:
        _finish(notification, Notification.Status.FAILED, error=str(exc))
    except NotificationRetry as exc:
        if notification.attempts >= MAX_ATTEMPTS:
            _finish(notification, Notification.Status.FAILED, error=f"Abandon : {exc}")
            return
        _finish(notification, Notification.Status.PENDING, error=str(exc))
        raise
    except Exception as exc:  # bug inattendu : on trace sans boucler
        logger.exception("Erreur envoi notification %s", notification_id)
        _finish(notification, Notification.Status.FAILED, error=repr(exc))
    else:
        _finish(
            notification, Notification.Status.SENT,
            provider_message_id=message_id or '', sent_at=timezone.now(),
        )


def _finish(notification, status, **fields):
    fields.setdefault('error', '')
    Notification.objects.filter(pk=notification.pk).update(status=status, **fields)


# ---------- Firebase Cloud Messaging ----------
_firebase_app = None


def _get_firebase_app():
    global _firebase_app
    if not settings.FIREBASE_CREDENTIALS:
        return None
    if _firebase_app is None:
        import firebase_admin
        from firebase_admin import credentials
        try:
            _firebase_app = firebase_admin.get_app('keeper')
        except ValueError:
            _firebase_app = firebase_admin.initialize_app(
                credentials.Certificate(settings.FIREBASE_CREDENTIALS), name='keeper',
            )
    return _firebase_app


def _send_push(notification):
    token = notification.user.fcm_token
    if not token:
        raise NotificationSkipped("Aucun token FCM pour cet utilisateur.")
    app = _get_firebase_app()
    if app is None:
        raise NotificationSkipped("FIREBASE_CREDENTIALS non configuré.")

    from firebase_admin import exceptions as fb_exceptions
    from firebase_admin import messaging

    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=notification.title, body=notification.body),
        # FCM n'accepte que des chaînes dans data
        data={k: str(v) for k, v in (notification.data or {}).items()},
        android=messaging.AndroidConfig(priority='high'),
        apns=messaging.APNSConfig(headers={'apns-priority': '10'}),
    )
    retryable = tuple(
        exc for exc in (
            getattr(fb_exceptions, 'UnavailableError', None),
            getattr(fb_exceptions, 'InternalError', None),
            getattr(fb_exceptions, 'DeadlineExceededError', None),
            getattr(fb_exceptions, 'ResourceExhaustedError', None),
        ) if exc
    )
    try:
        return messaging.send(message, app=app)
    except messaging.UnregisteredError:
        # Token mort (app désinstallée) : on le purge pour ne plus réessayer
        User.objects.filter(pk=notification.user_id, fcm_token=token).update(fcm_token='')
        raise NotificationFailed("Token FCM invalide, supprimé.")
    except retryable as exc:
        raise NotificationRetry(str(exc))
    except fb_exceptions.FirebaseError as exc:
        raise NotificationFailed(str(exc))


# ---------- WhatsApp Cloud API ----------
def _send_whatsapp(notification):
    cfg = settings.WHATSAPP
    if not (cfg['TOKEN'] and cfg['PHONE_NUMBER_ID']):
        raise NotificationSkipped("WhatsApp non configuré.")
    user = notification.user
    phone = re.sub(r'\D', '', user.whatsapp or user.phone or '')
    if not phone:
        raise NotificationSkipped("Aucun numéro WhatsApp.")

    text = f"*{notification.title}*\n{notification.body}" if notification.title else notification.body
    url = f"https://graph.facebook.com/{cfg['API_VERSION']}/{cfg['PHONE_NUMBER_ID']}/messages"
    try:
        response = requests.post(
            url,
            json={
                'messaging_product': 'whatsapp',
                'to': phone,
                'type': 'text',
                'text': {'preview_url': False, 'body': text},
            },
            headers={'Authorization': f"Bearer {cfg['TOKEN']}"},
            timeout=cfg['TIMEOUT_S'],
        )
    except requests.RequestException as exc:
        raise NotificationRetry(str(exc))

    if response.status_code == 429 or response.status_code >= 500:
        raise NotificationRetry(f"HTTP {response.status_code}")
    if response.status_code >= 400:
        raise NotificationFailed(f"HTTP {response.status_code}: {response.text[:500]}")
    messages = response.json().get('messages') or [{}]
    return messages[0].get('id', '')
