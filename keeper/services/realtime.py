"""
Diffusion WebSocket (Django Channels).
Groupes :
  user_<id>  → toutes les notifications d'un utilisateur (offres chauffeur, statut client…)
  ride_<id>  → suivi live d'une course (position, statut) pour client/chauffeur/admin
Toujours appeler via transaction.on_commit() depuis un service : on ne diffuse
jamais un état qui pourrait être annulé par un rollback.
"""
import json
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.serializers.json import DjangoJSONEncoder

logger = logging.getLogger(__name__)


def user_group(user_id):
    return f"user_{user_id}"


def ride_group(ride_id):
    return f"ride_{ride_id}"


def _jsonable(data):
    # channels_redis sérialise en msgpack : pas de Decimal/datetime
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def group_send(group, event, data):
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(
            group, {'type': 'broadcast', 'event': event, 'data': _jsonable(data)},
        )
    except Exception:  # le temps réel ne doit jamais casser une requête métier
        logger.exception("Échec diffusion WebSocket groupe=%s event=%s", group, event)


def send_to_user(user_id, event, data):
    group_send(user_group(user_id), event, data)


def send_to_ride(ride_id, event, data):
    group_send(ride_group(ride_id), event, data)
