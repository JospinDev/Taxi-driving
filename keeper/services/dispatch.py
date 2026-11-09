"""
Module 5 — Rides & Dispatch.

Ordre de verrouillage (TOUJOURS le même pour éviter les deadlocks) :
    Ride  →  RideOffer  →  DriverProfile  →  Wallet
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from rest_framework.exceptions import NotFound

from keeper.models import (
    Contract, DriverProfile, PricingRule, Ride, RideOffer, User, Vehicle,
)
from keeper.services import notifications, realtime
from keeper.services.exceptions import BusinessError, ConflictError
from keeper.services.geo import estimate_road_distance_km, point_to_dict
from keeper.services.locks import redis_lock

logger = logging.getLogger(__name__)

CFG = settings.DISPATCH


# ============================================================
# HELPERS
# ============================================================
def get_active_pricing_rule():
    rule = PricingRule.objects.filter(is_active=True).first()
    if rule is None:
        raise BusinessError("Aucune grille tarifaire active. Contactez l'administrateur.")
    return rule


def lock_ride(ride_id):
    """SELECT … FOR UPDATE sur la course seule (pas sur les tables jointes)."""
    ride = (
        Ride.objects.select_for_update(of=('self',))
        .select_related('pricing_rule')
        .filter(pk=ride_id)
        .first()
    )
    if ride is None:
        raise NotFound("Course introuvable.")
    return ride


def lock_driver_ride(ride_id, driver):
    ride = lock_ride(ride_id)
    if ride.driver_id != driver.pk:
        raise NotFound("Course introuvable.")
    return ride


def release_driver(driver_id):
    """Remet le chauffeur disponible après une course (dans la transaction appelante)."""
    profile = DriverProfile.objects.select_for_update().get(pk=driver_id)
    if profile.status == DriverProfile.Status.BUSY:
        profile.status = (
            DriverProfile.Status.AVAILABLE if profile.is_approved else DriverProfile.Status.PENDING
        )
        profile.save(update_fields=['status', 'updated_at'])
    return profile


def driver_context(ride):
    driver_user = ride.driver.user
    vehicle = ride.vehicle
    return {
        'client_name': ride.client.first_name or ride.client.username,
        'driver_name': driver_user.get_full_name() or driver_user.username,
        'driver_phone': driver_user.phone,
        'vehicle': f"{vehicle.brand} {vehicle.model}" if vehicle else '',
        'color': vehicle.color if vehicle else '',
        'plate': vehicle.plate_number if vehicle else '',
    }


def ride_status_payload(ride):
    return {
        'ride_id': ride.pk,
        'status': ride.status,
        'driver_id': ride.driver_id,
        'updated_at': timezone.now(),
    }


def broadcast_status(ride):
    """Diffuse le nouveau statut (client + suivi live) après commit."""
    payload = ride_status_payload(ride)
    client_id = ride.client_id
    transaction.on_commit(lambda: (
        realtime.send_to_ride(ride.pk, 'ride.status', payload),
        realtime.send_to_user(client_id, 'ride.status', payload),
    ))


def schedule_dispatch(ride_id, dispatch_round, wave, countdown=0):
    from keeper.tasks.dispatch import dispatch_ride
    try:
        dispatch_ride.apply_async((ride_id, dispatch_round, wave), countdown=countdown)
    except Exception:
        logger.exception("Impossible de programmer le dispatch de la course %s", ride_id)


# ============================================================
# ESTIMATION / CRÉATION
# ============================================================
def estimate(pickup, dropoff, rule=None):
    rule = rule or get_active_pricing_rule()
    distance_km = estimate_road_distance_km(pickup, dropoff)
    fare = rule.compute_fare(distance_km, 0)
    return {
        'pricing_rule': rule,
        'distance_km': distance_km,
        'fare': fare['total_rounded'],
        'breakdown': fare,
    }


@transaction.atomic
def create_ride(client, *, pickup, dropoff, pickup_address='', dropoff_address='',
                payment_method=Ride.PaymentMethod.CASH):
    # Verrou sur le client : sérialise les doubles clics « Commander »
    User.objects.select_for_update().filter(pk=client.pk).exists()
    if Ride.objects.filter(client=client, status__in=Ride.ACTIVE_STATUSES).exists():
        raise ConflictError("Vous avez déjà une course en cours.")

    est = estimate(pickup, dropoff)
    ride = Ride.objects.create(
        client=client,
        pricing_rule=est['pricing_rule'],
        pickup_location=pickup, pickup_address=pickup_address,
        dropoff_location=dropoff, dropoff_address=dropoff_address,
        estimated_distance_km=est['distance_km'],
        estimated_fare=est['fare'],
        payment_method=payment_method,
    )
    transaction.on_commit(lambda: schedule_dispatch(ride.pk, ride.dispatch_round, 0))
    return ride


# ============================================================
# RECHERCHE DES CHAUFFEURS (PostGIS)
# ============================================================
def find_candidate_drivers(ride, radius_m, limit):
    """
    Chauffeurs disponibles, approuvés, position fraîche, dans le rayon,
    avec véhicule actif + contrat signé actif, non encore sollicités pour
    cette course et sans autre offre en attente. Triés par distance.
    ST_DWithin sur geography exploite l'index GiST de last_location.
    """
    now = timezone.now()
    fresh_since = now - timedelta(seconds=CFG['LOCATION_MAX_AGE_S'])
    offer_window = now - timedelta(seconds=CFG['OFFER_TIMEOUT_S'])

    has_vehicle = Vehicle.objects.filter(driver=OuterRef('pk'), is_active=True)
    has_contract = Contract.objects.filter(
        driver=OuterRef('pk'), is_active=True, signed_at__isnull=False,
    )
    busy_elsewhere = RideOffer.objects.filter(
        driver=OuterRef('pk'),
        status=RideOffer.Status.PENDING,
        created_at__gte=offer_window,
    ).exclude(ride_id=ride.pk)
    # Déjà sollicités pour cette course : on exclut ceux qui ont une offre en
    # cours ou qui ont refusé / se sont désistés. Une offre EXPIRÉE (course
    # prise par un autre puis relâchée) peut être re-proposée.
    already_offered = RideOffer.objects.filter(ride_id=ride.pk).exclude(
        status=RideOffer.Status.EXPIRED,
    ).values('driver_id')

    return list(
        DriverProfile.objects
        .filter(
            status=DriverProfile.Status.AVAILABLE,
            is_approved=True,
            user__is_active=True,
            last_location_at__gte=fresh_since,
            last_location__dwithin=(ride.pickup_location, D(m=radius_m)),
        )
        .filter(Exists(has_vehicle), Exists(has_contract))
        .exclude(Exists(busy_elsewhere))
        .exclude(pk__in=already_offered)
        .annotate(distance=Distance('last_location', ride.pickup_location))
        .select_related('user')
        .order_by('distance')[:limit]
    )


def nearby_available_drivers(point, radius_m=5000, limit=30):
    """
    Taxis disponibles autour d'un point, pour l'affichage client.
    Anonymisé : seulement une position et une distance (ni nom, ni téléphone).
    """
    fresh_since = timezone.now() - timedelta(seconds=CFG['LOCATION_MAX_AGE_S'])
    qs = (
        DriverProfile.objects
        .filter(
            status=DriverProfile.Status.AVAILABLE,
            is_approved=True,
            last_location_at__gte=fresh_since,
            last_location__dwithin=(point, D(m=radius_m)),
        )
        .filter(
            Exists(Vehicle.objects.filter(driver=OuterRef('pk'), is_active=True)),
            Exists(Contract.objects.filter(
                driver=OuterRef('pk'), is_active=True, signed_at__isnull=False,
            )),
        )
        .annotate(distance=Distance('last_location', point))
        .order_by('distance')
        .only('id', 'last_location')[:limit]
    )
    return [
        {
            'id': d.pk,
            'lat': d.last_location.y,
            'lng': d.last_location.x,
            'distance_m': round(d.distance.m),
        }
        for d in qs
    ]


def run_dispatch_wave(ride_id, dispatch_round, wave):
    """
    Exécute une vague de dispatch. Retourne le délai (s) avant la vague
    suivante, ou None si le dispatch est terminé.
    Idempotent : ignoré si la course a changé d'état ou de tour de dispatch.
    """
    with transaction.atomic():
        ride = Ride.objects.select_for_update(of=('self',)).filter(pk=ride_id).first()
        if (
            ride is None
            or ride.status != Ride.Status.SEARCHING
            or ride.dispatch_round != dispatch_round
            or ride.dispatch_wave != wave
        ):
            return None

        radii = CFG['SEARCH_RADII_M']
        if wave >= len(radii):
            _mark_no_driver(ride)
            return None

        drivers = find_candidate_drivers(ride, radii[wave], CFG['DRIVERS_PER_WAVE'])
        ride.dispatch_wave = wave + 1
        ride.save(update_fields=['dispatch_wave', 'updated_at'])

        # Upsert : ré-ouvre une offre expirée au lieu de violer (ride, driver) unique
        RideOffer.objects.bulk_create(
            [
                RideOffer(ride=ride, driver=d, wave=wave + 1, distance_m=d.distance.m,
                          status=RideOffer.Status.PENDING, responded_at=None)
                for d in drivers
            ],
            update_conflicts=True,
            unique_fields=['ride', 'driver'],
            update_fields=['wave', 'distance_m', 'status', 'responded_at', 'created_at'],
        )
        for driver in drivers:
            ctx = {
                'pickup_distance_km': round(driver.distance.km, 1),
                'pickup_address': ride.pickup_address or "position du client",
                'estimated_fare': ride.estimated_fare,
            }
            notifications.notify(driver.user, 'ride_offer', ride=ride, context=ctx)
            offer_payload = {
                'ride_id': ride.pk,
                'pickup': point_to_dict(ride.pickup_location),
                'pickup_address': ride.pickup_address,
                'dropoff': point_to_dict(ride.dropoff_location),
                'dropoff_address': ride.dropoff_address,
                'estimated_fare': ride.estimated_fare,
                'estimated_distance_km': ride.estimated_distance_km,
                'distance_to_pickup_m': round(driver.distance.m),
                'expires_in_s': CFG['OFFER_TIMEOUT_S'],
            }
            transaction.on_commit(
                lambda uid=driver.user_id, p=offer_payload: realtime.send_to_user(uid, 'ride.offer', p)
            )

    return CFG['OFFER_TIMEOUT_S'] if drivers else CFG['EMPTY_WAVE_RETRY_S']


def _mark_no_driver(ride):
    now = timezone.now()
    ride.status = Ride.Status.NO_DRIVER
    ride.cancelled_by = Ride.CancelledBy.SYSTEM
    ride.cancelled_at = now
    ride.save(update_fields=['status', 'cancelled_by', 'cancelled_at', 'updated_at'])
    _close_offers(ride, RideOffer.Status.EXPIRED)
    notifications.notify(
        ride.client, 'no_driver', ride=ride,
        channels=(notifications.PUSH, notifications.WHATSAPP),
    )
    broadcast_status(ride)


def _close_offers(ride, status, exclude_offer_id=None):
    """Ferme les offres en attente et prévient les chauffeurs concernés (WS)."""
    pending = RideOffer.objects.filter(ride=ride, status=RideOffer.Status.PENDING)
    if exclude_offer_id:
        pending = pending.exclude(pk=exclude_offer_id)
    user_ids = list(pending.values_list('driver__user_id', flat=True))
    pending.update(status=status, responded_at=timezone.now())
    if user_ids:
        transaction.on_commit(lambda: [
            realtime.send_to_user(uid, 'ride.offer_closed', {'ride_id': ride.pk})
            for uid in user_ids
        ])


# ============================================================
# ACCEPTATION (Redis lock + SELECT FOR UPDATE)
# ============================================================
def accept_ride(ride_id, driver):
    timeout = CFG['ACCEPT_LOCK_TIMEOUT_S']
    # 1) Verrous Redis : échec immédiat si un autre chauffeur accepte la même
    #    course, ou si ce chauffeur accepte deux courses en même temps.
    with redis_lock(f"ride:{ride_id}:accept", timeout, "Un autre chauffeur est en train d'accepter cette course."), \
            redis_lock(f"driver:{driver.pk}:accept", timeout, "Acceptation déjà en cours."):
        # 2) Transaction + verrous de ligne : vérité en base
        with transaction.atomic():
            ride = lock_ride(ride_id)
            if ride.status != Ride.Status.SEARCHING:
                raise ConflictError("Cette course n'est plus disponible.")

            offer = (
                RideOffer.objects.select_for_update()
                .filter(ride=ride, driver=driver).first()
            )
            if offer is None or offer.status != RideOffer.Status.PENDING:
                raise BusinessError("Aucune offre en attente pour vous sur cette course.")

            profile = DriverProfile.objects.select_for_update().select_related('user').get(pk=driver.pk)
            if not profile.is_approved or profile.status != DriverProfile.Status.AVAILABLE:
                raise ConflictError("Vous devez être disponible pour accepter une course.")

            vehicle = (
                Vehicle.objects.filter(driver=profile, is_active=True)
                .order_by('-updated_at').first()
            )
            contract = Contract.objects.filter(
                driver=profile, is_active=True, signed_at__isnull=False,
            ).first()
            if vehicle is None:
                raise BusinessError("Aucun véhicule actif.")
            if contract is None:
                raise BusinessError("Aucun contrat signé et actif.")

            now = timezone.now()
            ride.driver = profile
            ride.vehicle = vehicle
            ride.contract = contract
            ride.commission_percent = contract.commission_percent
            ride.status = Ride.Status.ACCEPTED
            ride.accepted_at = now
            ride.save(update_fields=[
                'driver', 'vehicle', 'contract', 'commission_percent',
                'status', 'accepted_at', 'updated_at',
            ])

            profile.status = DriverProfile.Status.BUSY
            profile.save(update_fields=['status', 'updated_at'])

            offer.status = RideOffer.Status.ACCEPTED
            offer.responded_at = now
            offer.save(update_fields=['status', 'responded_at'])
            _close_offers(ride, RideOffer.Status.EXPIRED, exclude_offer_id=offer.pk)

            notifications.notify(
                ride.client, 'driver_assigned', ride=ride,
                channels=(notifications.PUSH, notifications.WHATSAPP),
                context=driver_context(ride),
            )
            broadcast_status(ride)
    return ride


def reject_offer(ride_id, driver):
    with transaction.atomic():
        ride = lock_ride(ride_id)
        offer = (
            RideOffer.objects.select_for_update()
            .filter(ride=ride, driver=driver, status=RideOffer.Status.PENDING).first()
        )
        if offer is None:
            raise BusinessError("Aucune offre en attente pour vous sur cette course.")
        offer.status = RideOffer.Status.REJECTED
        offer.responded_at = timezone.now()
        offer.save(update_fields=['status', 'responded_at'])

        # Tout le monde a refusé → on n'attend pas le timeout, vague suivante
        if (
            ride.status == Ride.Status.SEARCHING
            and not ride.offers.filter(status=RideOffer.Status.PENDING).exists()
        ):
            args = (ride.pk, ride.dispatch_round, ride.dispatch_wave)
            transaction.on_commit(lambda: schedule_dispatch(*args))
    return offer


# ============================================================
# ANNULATION
# ============================================================
CLIENT_CANCELLABLE = (Ride.Status.SEARCHING, Ride.Status.ACCEPTED, Ride.Status.ARRIVED)
DRIVER_CANCELLABLE = (Ride.Status.ACCEPTED, Ride.Status.ARRIVED)


@transaction.atomic
def cancel_ride(ride_id, user, reason=''):
    ride = lock_ride(ride_id)
    driver_profile = getattr(user, 'driver_profile', None) if user.is_driver else None

    if user.is_admin_role:
        actor = Ride.CancelledBy.ADMIN
    elif ride.client_id == user.pk:
        actor = Ride.CancelledBy.CLIENT
    elif driver_profile is not None and ride.driver_id == driver_profile.pk:
        actor = Ride.CancelledBy.DRIVER
    else:
        raise NotFound("Course introuvable.")

    if actor == Ride.CancelledBy.DRIVER:
        if ride.status not in DRIVER_CANCELLABLE:
            raise BusinessError("Vous ne pouvez plus annuler cette course.")
        return _requeue_after_driver_cancel(ride, reason)

    allowed = CLIENT_CANCELLABLE if actor == Ride.CancelledBy.CLIENT else Ride.ACTIVE_STATUSES
    if ride.status not in allowed:
        raise BusinessError("Cette course ne peut plus être annulée.")

    previous_driver_id = ride.driver_id
    ride.status = Ride.Status.CANCELLED
    ride.cancelled_by = actor
    ride.cancel_reason = reason
    ride.cancelled_at = timezone.now()
    ride.save(update_fields=['status', 'cancelled_by', 'cancel_reason', 'cancelled_at', 'updated_at'])
    _close_offers(ride, RideOffer.Status.CANCELLED)

    if previous_driver_id:
        profile = release_driver(previous_driver_id)
        notifications.notify(
            profile.user, 'ride_cancelled', ride=ride,
            context={'reason': reason},
        )
        transaction.on_commit(lambda: realtime.send_to_user(
            profile.user_id, 'ride.status', ride_status_payload(ride),
        ))
    broadcast_status(ride)
    return ride


def _requeue_after_driver_cancel(ride, reason):
    """Le chauffeur se désiste : la course repart en recherche (sans lui)."""
    driver_id = ride.driver_id
    RideOffer.objects.filter(ride=ride, driver_id=driver_id).update(
        status=RideOffer.Status.CANCELLED, responded_at=timezone.now(),
    )
    ride.status = Ride.Status.SEARCHING
    ride.driver = ride.vehicle = ride.contract = None
    ride.commission_percent = None
    ride.accepted_at = ride.arrived_at = None
    ride.dispatch_round += 1
    ride.dispatch_wave = 0
    ride.cancel_reason = reason
    ride.save(update_fields=[
        'status', 'driver', 'vehicle', 'contract', 'commission_percent',
        'accepted_at', 'arrived_at', 'dispatch_round', 'dispatch_wave',
        'cancel_reason', 'updated_at',
    ])
    release_driver(driver_id)
    notifications.notify(ride.client, 'driver_cancelled', ride=ride)
    broadcast_status(ride)
    args = (ride.pk, ride.dispatch_round, 0)
    transaction.on_commit(lambda: schedule_dispatch(*args))
    return ride

