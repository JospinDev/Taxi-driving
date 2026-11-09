import threading
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from keeper.models import (
    DriverProfile, Notification, Ride, RideOffer, RideTrackPoint, Wallet, WalletTransaction,
)
from keeper.services import dispatch, notifications, payments
from keeper.services.exceptions import ConflictError
from keeper.services.geo import make_point
from keeper.tests.factories import (
    DROPOFF, PICKUP, make_admin, make_client, make_driver, make_pricing_rule,
)

STEP = 0.001  # ≈ 111.195 m en latitude


def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


class RideFlowTests(TestCase):
    def setUp(self):
        self.rule = make_pricing_rule()
        self.client_user = make_client()
        self.driver = make_driver()
        self.driver2 = make_driver(lat=PICKUP['lat'] + 0.003)

    def _create_ride(self, **extra):
        res = api(self.client_user).post('/api/v1/rides/', {
            'pickup': PICKUP, 'dropoff': DROPOFF, 'pickup_address': 'Rohero', **extra,
        }, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        return Ride.objects.get(pk=res.data['id'])

    def _accept(self, ride, driver=None):
        dispatch.run_dispatch_wave(ride.pk, ride.dispatch_round, 0)
        res = api((driver or self.driver).user).post(f'/api/v1/rides/{ride.pk}/accept/')
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def _start(self, ride):
        d = api(self.driver.user)
        self.assertEqual(d.post(f'/api/v1/rides/{ride.pk}/arrived/').status_code, 200)
        self.assertEqual(d.post(f'/api/v1/rides/{ride.pk}/start/').status_code, 200)
        # On recule le départ pour pouvoir envoyer 3 minutes de GPS « passé »
        t0 = timezone.now() - timedelta(minutes=10)
        Ride.objects.filter(pk=ride.pk).update(started_at=t0)
        return t0

    # ------------------------------------------------------------------
    def test_full_flow_cash(self):
        ride = self._create_ride()
        self.assertEqual(ride.status, Ride.Status.SEARCHING)
        self.assertEqual(ride.pricing_rule, self.rule)

        # Dispatch : les 2 chauffeurs proches reçoivent l'offre, triés par distance
        dispatch.run_dispatch_wave(ride.pk, 0, 0)
        offers = list(RideOffer.objects.filter(ride=ride).order_by('distance_m'))
        self.assertEqual([o.driver_id for o in offers], [self.driver.pk, self.driver2.pk])

        res = api(self.driver.user).post(f'/api/v1/rides/{ride.pk}/accept/')
        self.assertEqual(res.status_code, 200, res.data)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.status, DriverProfile.Status.BUSY)

        # Le 2e chauffeur arrive trop tard → 409
        res = api(self.driver2.user).post(f'/api/v1/rides/{ride.pk}/accept/')
        self.assertEqual(res.status_code, 409)
        self.assertEqual(
            RideOffer.objects.get(ride=ride, driver=self.driver2).status, RideOffer.Status.EXPIRED,
        )

        # Client notifié (push + WhatsApp)
        self.assertEqual(
            set(Notification.objects.filter(user=self.client_user, event='driver_assigned')
                .values_list('channel', flat=True)),
            {'in_app', 'push', 'whatsapp'},
        )

        t0 = self._start(ride)
        lat, lng = PICKUP['lat'], PICKUP['lng']
        points, t = [], 0
        points.append({'lat': lat, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=1)})
        for _ in range(10):            # 10 × 111 m en 10 s = 40 km/h
            t += 10
            lat += STEP
            points.append({'lat': lat, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=1 + t)})
        for _ in range(6):             # 60 s à l'arrêt = embouteillage
            t += 10
            points.append({'lat': lat, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=1 + t)})
        t += 10                        # saut GPS aberrant (11 km en 10 s) → ignoré
        points.append({'lat': lat + 0.1, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=1 + t)})
        t += 10                        # on repart
        lat += STEP
        points.append({'lat': lat, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=1 + t)})

        # Envoi en 2 lots, désordonnés, avec un doublon
        d = api(self.driver.user)
        res = d.post(f'/api/v1/rides/{ride.pk}/track/', {'points': points[10:][::-1]}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        res = d.post(f'/api/v1/rides/{ride.pk}/track/', {'points': points[:11]}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['accepted'], 0)
        # Le 2e lot est antérieur au dernier point reçu → ignoré : on renvoie tout dans l'ordre
        RideTrackPoint.objects.filter(ride=ride).delete()
        Ride.objects.filter(pk=ride.pk).update(
            distance_m=0, traffic_seconds=0, slow_streak_seconds=0,
            last_track_location=None, last_track_at=None, track_points_count=0,
        )
        res = d.post(f'/api/v1/rides/{ride.pk}/track/', {'points': points}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['accepted'], len(points) - 1)  # saut GPS exclu

        ride.refresh_from_db()
        self.assertAlmostEqual(ride.distance_m, 11 * 111.195, delta=1)
        self.assertEqual(ride.traffic_seconds, 60)

        res = d.post(f'/api/v1/rides/{ride.pk}/complete/')
        self.assertEqual(res.status_code, 200, res.data)
        ride.refresh_from_db()
        # 1000 + 1.223 km × 1000 + 1 min × 100 = 2323 → arrondi 50 → 2300
        self.assertEqual(ride.actual_distance_km, Decimal('1.223'))
        self.assertEqual(ride.traffic_minutes, Decimal('1.00'))
        self.assertEqual(ride.fare_total, Decimal('2300'))
        self.assertEqual(ride.commission_amount, Decimal('460.00'))
        self.assertEqual(ride.driver_earning, Decimal('1840.00'))
        self.assertEqual(ride.payment_status, Ride.PaymentStatus.PAID)
        self.assertFalse(ride.distance_fallback)

        # Espèces : la commission est due par le chauffeur
        wallet = Wallet.objects.get(driver=self.driver)
        self.assertEqual(wallet.balance, Decimal('-460.00'))
        self.assertEqual(wallet.total_earned, Decimal('1840.00'))
        tx = WalletTransaction.objects.get(wallet=wallet)
        self.assertEqual((tx.type, tx.amount, tx.balance_after),
                         ('commission', Decimal('-460.00'), Decimal('-460.00')))

        self.driver.refresh_from_db()
        self.assertEqual(self.driver.status, DriverProfile.Status.AVAILABLE)
        self.assertEqual(self.driver.total_rides, 1)
        self.assertTrue(Notification.objects.filter(
            user=self.client_user, event='ride_invoice', channel='whatsapp').exists())

        # Le client ne voit pas la commission
        res = api(self.client_user).get(f'/api/v1/rides/{ride.pk}/')
        self.assertNotIn('commission_amount', res.data)

        # Notation
        res = api(self.client_user).post(f'/api/v1/rides/{ride.pk}/rate/', {'rating': 4}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.rating, Decimal('4.00'))
        res = api(self.client_user).post(f'/api/v1/rides/{ride.pk}/rate/', {'rating': 5}, format='json')
        self.assertEqual(res.status_code, 409)

    def test_short_traffic_is_not_billed(self):
        ride = self._create_ride()
        self._accept(ride)
        t0 = self._start(ride)
        lat, lng = PICKUP['lat'], PICKUP['lng']
        pts = [{'lat': lat, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=1)}]
        for s in (11, 21):             # 20 s à l'arrêt < 30 s minimum
            pts.append({'lat': lat, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=s)})
        pts.append({'lat': lat + STEP, 'lng': lng, 'recorded_at': t0 + timedelta(seconds=31)})
        api(self.driver.user).post(f'/api/v1/rides/{ride.pk}/track/', {'points': pts}, format='json')
        ride.refresh_from_db()
        self.assertEqual(ride.traffic_seconds, 0)
        self.assertEqual(ride.slow_streak_seconds, 0)

    def test_no_gps_falls_back_to_estimate(self):
        ride = self._create_ride()
        self._accept(ride)
        self._start(ride)
        api(self.driver.user).post(f'/api/v1/rides/{ride.pk}/complete/')
        ride.refresh_from_db()
        self.assertTrue(ride.distance_fallback)
        self.assertEqual(ride.actual_distance_km, ride.estimated_distance_km)

    def test_client_cannot_order_twice(self):
        self._create_ride()
        res = api(self.client_user).post('/api/v1/rides/', {
            'pickup': PICKUP, 'dropoff': DROPOFF,
        }, format='json')
        self.assertEqual(res.status_code, 409)

    def test_driver_cancel_requeues_without_him(self):
        ride = self._create_ride()
        self._accept(ride)
        res = api(self.driver.user).post(f'/api/v1/rides/{ride.pk}/cancel/', {'reason': 'panne'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        ride.refresh_from_db()
        self.assertEqual(ride.status, Ride.Status.SEARCHING)
        self.assertIsNone(ride.driver_id)
        self.assertEqual(ride.dispatch_round, 1)
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.status, DriverProfile.Status.AVAILABLE)

        # Nouvelle vague : driver1 (désisté) exclu, driver2 (offre expirée) re-sollicité
        dispatch.run_dispatch_wave(ride.pk, 1, 0)
        statuses = dict(RideOffer.objects.filter(ride=ride).values_list('driver_id', 'status'))
        self.assertEqual(statuses, {self.driver.pk: 'cancelled', self.driver2.pk: 'pending'})
        res = api(self.driver.user).post(f'/api/v1/rides/{ride.pk}/accept/')
        self.assertEqual(res.status_code, 400)
        res = api(self.driver2.user).post(f'/api/v1/rides/{ride.pk}/accept/')
        self.assertEqual(res.status_code, 200, res.data)

    def test_client_cancel_frees_driver(self):
        ride = self._create_ride()
        self._accept(ride)
        res = api(self.client_user).post(f'/api/v1/rides/{ride.pk}/cancel/', {}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data['status'], 'cancelled')
        self.driver.refresh_from_db()
        self.assertEqual(self.driver.status, DriverProfile.Status.AVAILABLE)
        self.assertTrue(Notification.objects.filter(user=self.driver.user, event='ride_cancelled').exists())

    def test_stale_obsolete_dispatch_task_is_ignored(self):
        ride = self._create_ride()
        self.assertEqual(dispatch.run_dispatch_wave(ride.pk, 0, 0), 20)
        self.assertIsNone(dispatch.run_dispatch_wave(ride.pk, 0, 0))   # doublon
        self.assertIsNone(dispatch.run_dispatch_wave(ride.pk, 9, 1))   # vieux tour

    def test_no_driver_after_last_wave(self):
        DriverProfile.objects.update(status=DriverProfile.Status.OFFLINE)
        ride = self._create_ride()
        for wave in range(3):
            self.assertEqual(dispatch.run_dispatch_wave(ride.pk, 0, wave), 5)
        self.assertIsNone(dispatch.run_dispatch_wave(ride.pk, 0, 3))
        ride.refresh_from_db()
        self.assertEqual(ride.status, Ride.Status.NO_DRIVER)

    def test_far_or_stale_drivers_are_not_dispatched(self):
        far = make_driver(lat=PICKUP['lat'] + 0.2)       # ~22 km
        stale = make_driver()
        DriverProfile.objects.filter(pk=stale.pk).update(
            last_location_at=timezone.now() - timedelta(minutes=10))
        not_ready = make_driver(ready=False)
        DriverProfile.objects.filter(pk=not_ready.pk).update(status=DriverProfile.Status.AVAILABLE)
        ride = self._create_ride()
        for wave in range(3):
            dispatch.run_dispatch_wave(ride.pk, 0, wave)
        offered = set(RideOffer.objects.filter(ride=ride).values_list('driver_id', flat=True))
        self.assertEqual(offered, {self.driver.pk, self.driver2.pk})
        self.assertNotIn(far.pk, offered)

    def test_mobile_money_confirm_credits_wallet_once(self):
        ride = self._create_ride(payment_method='mobile_money')
        self._accept(ride)
        self._start(ride)
        api(self.driver.user).post(f'/api/v1/rides/{ride.pk}/complete/')
        wallet = Wallet.objects.get(driver=self.driver)
        self.assertEqual(wallet.balance, 0)

        admin = make_admin()
        res = api(admin).post(f'/api/v1/rides/{ride.pk}/confirm-payment/', {'reference': 'LUMI-1'}, format='json')
        self.assertEqual(res.status_code, 200, res.data)
        wallet.refresh_from_db()
        ride.refresh_from_db()
        self.assertEqual(wallet.balance, ride.driver_earning)
        res = api(admin).post(f'/api/v1/rides/{ride.pk}/confirm-payment/', {}, format='json')
        self.assertEqual(res.status_code, 409)

        # Versement : impossible au-delà du solde
        res = api(admin).post(f'/api/v1/payments/wallets/{wallet.pk}/payout/',
                              {'amount': '999999'}, format='json')
        self.assertEqual(res.status_code, 400)
        res = api(admin).post(f'/api/v1/payments/wallets/{wallet.pk}/payout/',
                              {'amount': str(wallet.balance), 'reference': 'V1'}, format='json')
        self.assertEqual(res.status_code, 201, res.data)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, 0)

        res = api(self.driver.user).get('/api/v1/payments/wallets/me/')
        self.assertEqual(res.status_code, 200)
        res = api(self.driver.user).get('/api/v1/payments/transactions/')
        self.assertEqual(res.data['count'], 2)

    def test_driver_cannot_go_available_without_vehicle(self):
        driver = make_driver(ready=False)
        res = api(driver.user).patch('/api/v1/accounts/drivers/me/status/', {'status': 'available'}, format='json')
        self.assertEqual(res.status_code, 400)

    def test_nearby_drivers_is_anonymous(self):
        res = api(self.client_user).get('/api/v1/rides/nearby-drivers/', PICKUP)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual([d['id'] for d in res.data], [self.driver.pk, self.driver2.pk])
        self.assertEqual(set(res.data[0]), {'id', 'lat', 'lng', 'distance_m'})
        self.assertEqual(api(self.driver.user).get('/api/v1/rides/nearby-drivers/', PICKUP).status_code, 403)

    def test_other_users_cannot_see_ride(self):
        ride = self._create_ride()
        other = make_client()
        self.assertEqual(api(other).get(f'/api/v1/rides/{ride.pk}/').status_code, 404)
        self.assertEqual(api(other).post(f'/api/v1/rides/{ride.pk}/cancel/', {}, format='json').status_code, 404)


class NotificationDeliveryTests(TestCase):
    def setUp(self):
        self.user = make_client()

    def _notif(self, channel):
        return Notification.objects.create(
            user=self.user, channel=channel, event='no_driver', title='T', body='B',
        )

    @override_settings(FIREBASE_CREDENTIALS='', WHATSAPP={'TOKEN': '', 'PHONE_NUMBER_ID': '',
                                                           'API_VERSION': 'v21.0', 'TIMEOUT_S': 1})
    def test_not_configured_is_skipped(self):
        n = self._notif('whatsapp')
        notifications.deliver(n.pk)
        n.refresh_from_db()
        self.assertEqual(n.status, 'skipped')
        self.assertEqual(n.attempts, 1)

    @override_settings(WHATSAPP={'TOKEN': 't', 'PHONE_NUMBER_ID': '123',
                                 'API_VERSION': 'v21.0', 'TIMEOUT_S': 1})
    def test_whatsapp_sent_and_not_sent_twice(self):
        n = self._notif('whatsapp')
        response = mock.Mock(status_code=200)
        response.json.return_value = {'messages': [{'id': 'wamid.1'}]}
        with mock.patch('keeper.services.notifications.requests.post', return_value=response) as post:
            notifications.deliver(n.pk)
            notifications.deliver(n.pk)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs['json']['to'], self.user.phone.lstrip('+'))
        n.refresh_from_db()
        self.assertEqual((n.status, n.provider_message_id), ('sent', 'wamid.1'))

    @override_settings(WHATSAPP={'TOKEN': 't', 'PHONE_NUMBER_ID': '123',
                                 'API_VERSION': 'v21.0', 'TIMEOUT_S': 1})
    def test_whatsapp_5xx_is_retried(self):
        n = self._notif('whatsapp')
        with mock.patch('keeper.services.notifications.requests.post',
                        return_value=mock.Mock(status_code=503)):
            with self.assertRaises(notifications.NotificationRetry):
                notifications.deliver(n.pk)
        n.refresh_from_db()
        self.assertEqual(n.status, 'pending')


@contextmanager
def no_side_effects():
    """Pas de Celery / WebSocket réels pendant les tests transactionnels."""
    with mock.patch('keeper.services.notifications.enqueue'), \
            mock.patch('keeper.services.dispatch.schedule_dispatch'), \
            mock.patch('keeper.services.realtime.group_send'):
        yield


class ConcurrentAcceptTests(TransactionTestCase):
    """Vrais commits + vrais threads : un seul chauffeur doit gagner."""

    def _race(self, n_drivers=6):
        make_pricing_rule()
        client = make_client()
        drivers = [make_driver(lat=PICKUP['lat'] + i * 0.0005) for i in range(n_drivers)]
        with no_side_effects():
            ride = dispatch.create_ride(
                client, pickup=make_point(PICKUP['lng'], PICKUP['lat']),
                dropoff=make_point(DROPOFF['lng'], DROPOFF['lat']),
            )
            with mock.patch.dict('django.conf.settings.DISPATCH', {'DRIVERS_PER_WAVE': n_drivers}):
                dispatch.run_dispatch_wave(ride.pk, 0, 0)

            barrier = threading.Barrier(n_drivers)
            results = []

            def worker(driver):
                try:
                    barrier.wait()
                    dispatch.accept_ride(ride.pk, driver)
                    results.append('ok')
                except ConflictError:
                    results.append('conflict')
                except Exception as exc:  # pragma: no cover
                    results.append(repr(exc))
                finally:
                    connection.close()

            threads = [threading.Thread(target=worker, args=(d,)) for d in drivers]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        return ride, drivers, results

    def _assert_single_winner(self, ride, drivers, results):
        self.assertEqual(results.count('ok'), 1, results)
        self.assertEqual(results.count('conflict'), len(drivers) - 1, results)
        ride.refresh_from_db()
        self.assertEqual(ride.status, Ride.Status.ACCEPTED)
        self.assertEqual(DriverProfile.objects.filter(status=DriverProfile.Status.BUSY).count(), 1)
        self.assertEqual(RideOffer.objects.filter(ride=ride, status='accepted').count(), 1)

    def test_redis_lock_single_winner(self):
        self._assert_single_winner(*self._race())

    def test_db_lock_single_winner_when_redis_down(self):
        @contextmanager
        def broken_lock(*args, **kwargs):
            yield  # Redis HS : on ne s'appuie plus que sur SELECT FOR UPDATE
        with mock.patch('keeper.services.dispatch.redis_lock', broken_lock):
            self._assert_single_winner(*self._race())


class WalletConcurrencyTests(TransactionTestCase):
    def test_concurrent_payouts_never_overdraw(self):
        driver = make_driver()
        admin = make_admin()
        wallet = Wallet.objects.get(driver=driver)
        payments.adjust(wallet.pk, Decimal('1000'), admin, 'dépôt')

        barrier = threading.Barrier(5)
        results = []

        def worker():
            try:
                barrier.wait()
                with no_side_effects():
                    payments.payout(wallet.pk, Decimal('300'), admin)
                results.append('ok')
            except Exception as exc:
                results.append(type(exc).__name__)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        wallet.refresh_from_db()
        self.assertEqual(results.count('ok'), 3, results)
        self.assertEqual(wallet.balance, Decimal('100'))
        self.assertEqual(
            wallet.transactions.order_by('-id').first().balance_after, Decimal('100'),
        )
