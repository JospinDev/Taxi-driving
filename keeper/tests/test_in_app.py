from unittest import mock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from keeper.models import Notification
from keeper.services import notifications
from keeper.tests.factories import make_admin, make_client, make_driver
from keeper.tests.test_rides import api


class InAppNotificationTests(TestCase):
    def setUp(self):
        self.user = make_client()

    def test_notify_creates_in_app_and_pushes_popup_after_commit(self):
        with mock.patch('keeper.services.realtime.send_to_user') as ws, \
                mock.patch('keeper.services.notifications.enqueue') as enqueue:
            with self.captureOnCommitCallbacks(execute=True):
                notifications.notify(
                    self.user, 'no_driver', channels=(notifications.PUSH, notifications.WHATSAPP),
                )
                ws.assert_not_called()   # rien avant le commit

        in_app = Notification.objects.get(user=self.user, channel='in_app')
        self.assertEqual(in_app.status, 'sent')          # jamais envoyée à Celery
        enqueued = enqueue.call_args.args[0]
        self.assertNotIn(in_app.pk, enqueued)
        self.assertEqual(len(enqueued), 2)

        user_id, event, payload = ws.call_args.args
        self.assertEqual((user_id, event), (self.user.pk, 'notification'))
        self.assertEqual(payload['id'], in_app.pk)
        self.assertEqual(payload['popup'], {'ttl_s': 8, 'priority': 'high'})
        self.assertEqual(payload['unread_count'], 1)

    def test_in_app_can_be_disabled(self):
        notifications.notify(self.user, 'ride_started', in_app=False)
        self.assertFalse(Notification.objects.filter(channel='in_app').exists())

    def test_inbox_unread_and_read(self):
        for _ in range(3):
            notifications.notify(self.user, 'ride_started', channels=(notifications.PUSH,))
        other = make_client()
        notifications.notify(other, 'ride_started')

        c = api(self.user)
        res = c.get('/api/v1/notifications/')
        self.assertEqual(res.data['count'], 3)          # in-app seulement, pas les push
        self.assertTrue(all(n['channel'] == 'in_app' for n in res.data['results']))
        self.assertNotIn('error', res.data['results'][0])
        self.assertEqual(c.get('/api/v1/notifications/unread-count/').data['unread_count'], 3)

        nid = res.data['results'][0]['id']
        res = c.post(f'/api/v1/notifications/{nid}/read/')
        self.assertTrue(res.data['is_read'])
        self.assertIsNotNone(res.data['read_at'])
        self.assertEqual(res.data['unread_count'], 2)
        c.post(f'/api/v1/notifications/{nid}/read/')     # idempotent
        self.assertEqual(c.get('/api/v1/notifications/unread-count/').data['unread_count'], 2)

        # Impossible de lire la notification d'un autre
        foreign = Notification.objects.get(user=other, channel='in_app')
        self.assertEqual(c.post(f'/api/v1/notifications/{foreign.pk}/read/').status_code, 404)

        self.assertEqual(c.post('/api/v1/notifications/read-all/').data['updated'], 2)
        self.assertEqual(c.get('/api/v1/notifications/unread-count/').data['unread_count'], 0)
        self.assertEqual(notifications.unread_count(other.pk), 1)

    def test_admin_sees_all_channels(self):
        notifications.notify(self.user, 'ride_started', channels=(notifications.PUSH,))
        res = api(make_admin()).get('/api/v1/notifications/?user=%d' % self.user.pk)
        self.assertEqual({n['channel'] for n in res.data['results']}, {'in_app', 'push'})

    def test_driver_gets_ride_offer_popup(self):
        driver = make_driver()
        notifications.notify(driver.user, 'ride_offer', context={'pickup_distance_km': 1.2})
        res = api(driver.user).get('/api/v1/notifications/')
        n = res.data['results'][0]
        self.assertEqual(n['event'], 'ride_offer')
        self.assertEqual(n['popup']['priority'], 'high')


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class InAppWebSocketTests(TransactionTestCase):
    def test_ws_receives_unread_count_then_popup(self):
        from config.asgi import application
        user = make_client()
        notifications.notify(user, 'ride_started', in_app=True, channels=())

        async def scenario():
            comm = WebsocketCommunicator(
                application, f'/ws/notifications/?token={AccessToken.for_user(user)}',
                headers=[(b'origin', b'http://localhost')],
            )
            connected, _ = await comm.connect()
            assert connected
            first = await comm.receive_json_from()
            from channels.db import database_sync_to_async
            await database_sync_to_async(
                lambda: notifications.notify(user, 'driver_arrived', channels=())
            )()
            second = await comm.receive_json_from(timeout=3)
            await comm.disconnect()
            return first, second

        first, second = async_to_sync(scenario)()
        self.assertEqual(first, {'event': 'notification.unread_count', 'data': {'unread_count': 1}})
        self.assertEqual(second['event'], 'notification')
        self.assertEqual(second['data']['event'], 'driver_arrived')
        self.assertEqual(second['data']['unread_count'], 2)
        self.assertEqual(second['data']['popup']['priority'], 'high')

    def test_ws_rejects_without_token(self):
        from config.asgi import application

        async def scenario():
            comm = WebsocketCommunicator(application, '/ws/notifications/',
                                         headers=[(b'origin', b'http://localhost')])
            connected, code = await comm.connect()
            return connected, code

        connected, code = async_to_sync(scenario)()
        self.assertFalse(connected)
        self.assertEqual(code, 4401)
