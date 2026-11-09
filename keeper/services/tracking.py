"""
Module 6 — Tracking & Lifecycle.

Les km réels et le temps d'embouteillage sont calculés de façon INCRÉMENTALE
à chaque lot de points GPS (agrégats stockés sur Ride) : la clôture n'a
jamais besoin de relire tout l'historique GPS.

Règle trafic (PricingRule) :
  vitesse < max_speed_kmh_for_traffic pendant au moins
  min_traffic_duration_seconds CONTINUES → toute la période lente est facturée.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from keeper.models import DriverProfile, Ride, RideTrackPoint
from keeper.services import notifications, realtime
from keeper.services.dispatch import broadcast_status, driver_context, lock_driver_ride
from keeper.services.exceptions import BusinessError
from keeper.services.geo import haversine_m, make_point

CFG = settings.TRACKING
TRACKABLE = (Ride.Status.ACCEPTED, Ride.Status.ARRIVED, Ride.Status.IN_PROGRESS)


# ============================================================
# TRANSITIONS DE STATUT (chauffeur)
# ============================================================
@transaction.atomic
def mark_arrived(ride_id, driver):
    ride = lock_driver_ride(ride_id, driver)
    if ride.status != Ride.Status.ACCEPTED:
        raise BusinessError("Transition impossible depuis le statut actuel.")
    ride.status = Ride.Status.ARRIVED
    ride.arrived_at = timezone.now()
    ride.save(update_fields=['status', 'arrived_at', 'updated_at'])
    notifications.notify(
        ride.client, 'driver_arrived', ride=ride,
        channels=(notifications.PUSH, notifications.WHATSAPP),
        context=driver_context(ride),
    )
    broadcast_status(ride)
    return ride


@transaction.atomic
def start_ride(ride_id, driver):
    ride = lock_driver_ride(ride_id, driver)
    if ride.status != Ride.Status.ARRIVED:
        raise BusinessError("Signalez d'abord votre arrivée au point de prise en charge.")
    now = timezone.now()
    ride.status = Ride.Status.IN_PROGRESS
    ride.started_at = now
    # Le compteur démarre ici : rien avant la prise en charge n'est facturé
    ride.distance_m = 0
    ride.traffic_seconds = 0
    ride.slow_streak_seconds = 0
    ride.last_track_location = None
    ride.last_track_at = None
    ride.track_points_count = 0
    ride.save(update_fields=[
        'status', 'started_at', 'distance_m', 'traffic_seconds', 'slow_streak_seconds',
        'last_track_location', 'last_track_at', 'track_points_count', 'updated_at',
    ])
    notifications.notify(ride.client, 'ride_started', ride=ride)
    broadcast_status(ride)
    return ride


# ============================================================
# TRACKING GPS
# ============================================================
def record_track_points(ride_id, driver, points):
    """
    points : liste de dict {lat, lng, recorded_at, accuracy?, heading?}
    (lot envoyé par l'app, ex. toutes les 5 s ou au retour du réseau).
    """
    now = timezone.now()
    max_future = now + timedelta(seconds=CFG['MAX_FUTURE_SKEW_S'])
    points = sorted(
        (p for p in points if p['recorded_at'] <= max_future),
        key=lambda p: p['recorded_at'],
    )
    if not points:
        raise BusinessError("Aucun point GPS valide.")

    with transaction.atomic():
        ride = lock_driver_ride(ride_id, driver)
        if ride.status not in TRACKABLE:
            raise BusinessError("La course n'est pas en cours.")

        accepted = 0
        if ride.status == Ride.Status.IN_PROGRESS:
            accepted = _accumulate(ride, points)

        # Position chauffeur (UPDATE direct, ordre de verrou Ride → DriverProfile)
        last = points[-1]
        last_point = make_point(last['lng'], last['lat'])
        DriverProfile.objects.filter(pk=driver.pk).update(
            last_location=last_point, last_location_at=now,
        )

        payload = {
            'ride_id': ride.pk,
            'status': ride.status,
            'lat': last['lat'],
            'lng': last['lng'],
            'heading': last.get('heading'),
            'recorded_at': last['recorded_at'],
            'distance_km': round(ride.distance_m / 1000, 3),
            'traffic_minutes': round(ride.traffic_seconds / 60, 2),
        }
        transaction.on_commit(lambda: realtime.send_to_ride(ride.pk, 'ride.location', payload))

    return {'received': len(points), 'accepted': accepted, **payload}


def _accumulate(ride, points):
    rule = ride.pricing_rule
    slow_kmh = rule.max_speed_kmh_for_traffic
    min_slow_s = rule.min_traffic_duration_seconds
    max_kmh = CFG['MAX_SPEED_KMH']
    min_move = CFG['MIN_MOVE_M']

    prev_loc = ride.last_track_location
    prev_at = ride.last_track_at
    distance = ride.distance_m
    traffic = ride.traffic_seconds
    streak = ride.slow_streak_seconds
    rows = []

    for p in points:
        at = p['recorded_at']
        if ride.started_at and at < ride.started_at:
            continue
        if prev_at is not None and at <= prev_at:
            continue  # doublon ou point hors ordre
        loc = make_point(p['lng'], p['lat'])
        speed = 0.0
        is_traffic = False

        if prev_loc is not None:
            dt = (at - prev_at).total_seconds()
            d = haversine_m(prev_loc, loc)
            speed = d / dt * 3.6
            if speed > max_kmh:
                continue  # saut GPS : on ignore le point, on garde le précédent
            if d < min_move:
                d, speed = 0.0, 0.0  # bruit GPS à l'arrêt
            distance += d

            if speed < slow_kmh:
                before = streak
                streak += dt
                if streak >= min_slow_s:
                    # Seuil franchi : on facture toute la période lente d'un coup
                    traffic += streak if before < min_slow_s else dt
                    is_traffic = True
            else:
                streak = 0.0

        rows.append(RideTrackPoint(
            ride=ride, location=loc, speed_kmh=round(speed, 2),
            accuracy_m=p.get('accuracy'), heading=p.get('heading'),
            is_traffic=is_traffic, recorded_at=at,
        ))
        prev_loc, prev_at = loc, at

    if rows:
        RideTrackPoint.objects.bulk_create(rows)
        ride.distance_m = distance
        ride.traffic_seconds = traffic
        ride.slow_streak_seconds = streak
        ride.last_track_location = prev_loc
        ride.last_track_at = prev_at
        ride.track_points_count += len(rows)
        ride.save(update_fields=[
            'distance_m', 'traffic_seconds', 'slow_streak_seconds',
            'last_track_location', 'last_track_at', 'track_points_count', 'updated_at',
        ])
    return len(rows)
