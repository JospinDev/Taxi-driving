"""
Module 7 — Payments & Wallet.

Toutes les écritures sur Wallet passent par _post() : ligne verrouillée
(SELECT … FOR UPDATE), journal WalletTransaction avec balance_after,
contrainte unique (ride, type) pour l'idempotence.

Espèces       : le chauffeur encaisse tout → on DÉBITE la commission (dette).
Mobile money  : la société encaisse → on CRÉDITE le gain du chauffeur
                à la confirmation du paiement.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Avg, F
from django.utils import timezone
from rest_framework.exceptions import NotFound

from keeper.models import DriverProfile, Ride, Wallet, WalletTransaction
from keeper.services import notifications, realtime
from keeper.services.dispatch import broadcast_status, lock_driver_ride, lock_ride, release_driver
from keeper.services.exceptions import BusinessError, ConflictError

CENT = Decimal('0.01')


# ============================================================
# PORTEFEUILLE
# ============================================================
def lock_wallet(driver_id):
    wallet = Wallet.objects.select_for_update().filter(driver_id=driver_id).first()
    if wallet is None:
        try:
            with transaction.atomic():  # savepoint : la course concurrente gagne
                Wallet.objects.create(driver_id=driver_id)
        except IntegrityError:
            pass
        wallet = Wallet.objects.select_for_update().get(driver_id=driver_id)
    return wallet


def _post(wallet, amount, tx_type, *, ride=None, description='', reference='', created_by=None):
    """Écrit un mouvement. `wallet` DOIT être verrouillé par l'appelant."""
    wallet.balance += amount
    wallet.save(update_fields=[
        'balance', 'total_earned', 'total_commission', 'total_paid_out', 'updated_at',
    ])
    try:
        with transaction.atomic():
            return WalletTransaction.objects.create(
                wallet=wallet, ride=ride, type=tx_type, amount=amount,
                balance_after=wallet.balance, description=description,
                reference=reference, created_by=created_by,
            )
    except IntegrityError:
        raise ConflictError("Cette course a déjà été réglée.")


# ============================================================
# CLÔTURE DE COURSE (calcul final + règlement)
# ============================================================
@transaction.atomic
def complete_ride(ride_id, driver):
    ride = lock_driver_ride(ride_id, driver)
    if ride.status != Ride.Status.IN_PROGRESS:
        raise BusinessError("La course n'est pas en cours.")

    # ---- 1. Distance & trafic réels ----
    if ride.track_points_count >= 2 and ride.distance_m > 0:
        distance_km = Decimal(str(ride.distance_m / 1000)).quantize(Decimal('0.001'))
        fallback = False
    else:
        # GPS absent : on facture l'estimation annoncée au client
        distance_km = ride.estimated_distance_km
        fallback = True
    traffic_minutes = Decimal(str(ride.traffic_seconds / 60)).quantize(CENT)

    # ---- 2. Prix (grille figée à la commande) ----
    fare = ride.pricing_rule.compute_fare(distance_km, traffic_minutes)
    total = fare['total_rounded']
    commission = (total * ride.commission_percent / Decimal('100')).quantize(CENT, ROUND_HALF_UP)
    earning = total - commission

    now = timezone.now()
    ride.status = Ride.Status.COMPLETED
    ride.completed_at = now
    ride.actual_distance_km = distance_km
    ride.traffic_minutes = traffic_minutes
    ride.distance_fallback = fallback
    ride.fare_base = fare['base_fare']
    ride.fare_km = fare['km_cost']
    ride.fare_traffic = fare['traffic_cost']
    ride.fare_subtotal = fare['subtotal']
    ride.fare_total = total
    ride.commission_amount = commission
    ride.driver_earning = earning

    # ---- 3. Chauffeur libéré + compteur ----
    release_driver(ride.driver_id)
    DriverProfile.objects.filter(pk=ride.driver_id).update(total_rides=F('total_rides') + 1)

    # ---- 4. Portefeuille ----
    wallet = lock_wallet(ride.driver_id)
    wallet.total_earned += earning
    wallet.total_commission += commission
    if ride.payment_method == Ride.PaymentMethod.CASH:
        ride.payment_status = Ride.PaymentStatus.PAID
        ride.paid_at = now
        _post(
            wallet, -commission, WalletTransaction.Type.COMMISSION, ride=ride,
            description=f"Commission {ride.commission_percent}% course #{ride.pk} (espèces)",
        )
    else:
        # Gain crédité à la confirmation du paiement (confirm_payment)
        wallet.save(update_fields=['total_earned', 'total_commission', 'updated_at'])

    ride.save()

    notifications.notify(
        ride.client, 'ride_invoice', ride=ride,
        channels=(notifications.PUSH, notifications.WHATSAPP),
        context={
            'distance_km': distance_km,
            'traffic_minutes': traffic_minutes,
            'fare_base': fare['base_fare'],
            'fare_km': fare['km_cost'],
            'fare_traffic': fare['traffic_cost'],
            'fare_total': total,
            'payment_method': ride.get_payment_method_display(),
        },
    )
    broadcast_status(ride)
    invoice = {'ride_id': ride.pk, 'fare_total': total, 'distance_km': distance_km,
               'traffic_minutes': traffic_minutes}
    transaction.on_commit(lambda: realtime.send_to_ride(ride.pk, 'ride.completed', invoice))
    return ride


@transaction.atomic
def confirm_payment(ride_id, admin, reference=''):
    """Paiement mobile money reçu par la société → crédit du gain chauffeur."""
    ride = lock_ride(ride_id)
    if ride.status != Ride.Status.COMPLETED:
        raise BusinessError("La course n'est pas terminée.")
    if ride.payment_method != Ride.PaymentMethod.MOBILE_MONEY:
        raise BusinessError("Seuls les paiements mobile money se confirment.")
    if ride.payment_status == Ride.PaymentStatus.PAID:
        raise ConflictError("Paiement déjà confirmé.")

    wallet = lock_wallet(ride.driver_id)
    _post(
        wallet, ride.driver_earning, WalletTransaction.Type.RIDE_EARNING, ride=ride,
        description=f"Gain course #{ride.pk}", reference=reference, created_by=admin,
    )
    ride.payment_status = Ride.PaymentStatus.PAID
    ride.payment_reference = reference
    ride.paid_at = timezone.now()
    ride.save(update_fields=['payment_status', 'payment_reference', 'paid_at', 'updated_at'])

    notifications.notify(
        ride.driver.user, 'payment_confirmed', ride=ride,
        context={'driver_earning': ride.driver_earning},
    )
    return ride


# ============================================================
# OPÉRATIONS ADMIN SUR LE PORTEFEUILLE
# ============================================================
def _lock_wallet_by_id(wallet_id):
    wallet = Wallet.objects.select_for_update().filter(pk=wallet_id).first()
    if wallet is None:
        raise NotFound("Portefeuille introuvable.")
    return wallet


@transaction.atomic
def payout(wallet_id, amount, admin, reference='', description=''):
    wallet = _lock_wallet_by_id(wallet_id)
    if amount <= 0:
        raise BusinessError("Le montant doit être positif.")
    if amount > wallet.balance:
        raise BusinessError(f"Solde insuffisant ({wallet.balance} {wallet.currency}).")
    wallet.total_paid_out += amount
    tx = _post(
        wallet, -amount, WalletTransaction.Type.PAYOUT,
        description=description or "Versement au chauffeur",
        reference=reference, created_by=admin,
    )
    notifications.notify(
        wallet.driver.user, 'wallet_payout',
        context={'amount': amount, 'reference': reference or '-'},
    )
    return tx


@transaction.atomic
def adjust(wallet_id, amount, admin, description, reference=''):
    """Ajustement manuel signé (ex: chauffeur rembourse sa dette de commission : +montant)."""
    wallet = _lock_wallet_by_id(wallet_id)
    if amount == 0:
        raise BusinessError("Le montant ne peut pas être nul.")
    return _post(
        wallet, amount, WalletTransaction.Type.ADJUSTMENT,
        description=description, reference=reference, created_by=admin,
    )


# ============================================================
# NOTATION DU CHAUFFEUR
# ============================================================
@transaction.atomic
def rate_ride(ride_id, client, rating, comment=''):
    ride = lock_ride(ride_id)
    if ride.client_id != client.pk:
        raise NotFound("Course introuvable.")
    if ride.status != Ride.Status.COMPLETED:
        raise BusinessError("Seule une course terminée peut être notée.")
    if ride.client_rating is not None:
        raise ConflictError("Course déjà notée.")

    ride.client_rating = rating
    ride.client_comment = comment
    ride.save(update_fields=['client_rating', 'client_comment', 'updated_at'])

    profile = DriverProfile.objects.select_for_update().get(pk=ride.driver_id)
    avg = Ride.objects.filter(
        driver=profile, client_rating__isnull=False,
    ).aggregate(avg=Avg('client_rating'))['avg']
    profile.rating = Decimal(str(avg)).quantize(CENT)
    profile.save(update_fields=['rating', 'updated_at'])
    return ride
