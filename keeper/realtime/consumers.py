"""
Consumers en lecture seule : le serveur pousse les événements.
Les actions (accepter, GPS…) passent par l'API HTTP, transactionnelle.

Format reçu par l'app : {"event": "ride.status", "data": {...}}
Événements :
  notification               → popup in-app (data.popup.ttl_s / priority) + unread_count
  notification.unread_count  → envoyé à la connexion (badge)
  ride.offer, ride.offer_closed, ride.status, ride.location, ride.completed
"""
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from keeper.services.realtime import ride_group, user_group


class _BaseConsumer(AsyncJsonWebsocketConsumer):
    group_name = None

    async def disconnect(self, code):
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        if content.get('type') == 'ping':
            await self.send_json({'event': 'pong'})

    async def broadcast(self, message):
        await self.send_json({'event': message['event'], 'data': message['data']})

    async def _join(self, group):
        self.group_name = group
        await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()


class NotificationConsumer(_BaseConsumer):
    """ws/notifications/ — flux personnel (offres chauffeur, statut des courses du client)."""

    async def connect(self):
        user = self.scope['user']
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        await self._join(user_group(user.pk))
        # Badge à jour dès la (re)connexion : l'app rattrape ce qu'elle a manqué hors ligne
        await self.send_json({
            'event': 'notification.unread_count',
            'data': {'unread_count': await self._unread(user.pk)},
        })

    @database_sync_to_async
    def _unread(self, user_id):
        from keeper.services.notifications import unread_count
        return unread_count(user_id)


class RideConsumer(_BaseConsumer):
    """ws/rides/<id>/ — suivi live d'une course (client, chauffeur assigné, admin)."""

    async def connect(self):
        user = self.scope['user']
        ride_id = self.scope['url_route']['kwargs']['ride_id']
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await self._can_follow(user, ride_id):
            await self.close(code=4403)
            return
        await self._join(ride_group(ride_id))

    @database_sync_to_async
    def _can_follow(self, user, ride_id):
        from keeper.models import Ride
        ride = Ride.objects.filter(pk=ride_id).only('client_id', 'driver_id').first()
        if ride is None:
            return False
        if user.is_admin_role or ride.client_id == user.pk:
            return True
        profile = getattr(user, 'driver_profile', None) if user.is_driver else None
        return profile is not None and ride.driver_id == profile.pk
