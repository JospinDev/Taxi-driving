import itertools
from decimal import Decimal

from django.utils import timezone

from keeper.models import Contract, DriverProfile, PricingRule, User, Vehicle, Wallet
from keeper.services.geo import make_point

_seq = itertools.count(1)

# Bujumbura centre
PICKUP = {'lat': -3.3822, 'lng': 29.3644}
DROPOFF = {'lat': -3.3700, 'lng': 29.3600}


def make_pricing_rule(**kwargs):
    defaults = dict(
        name=f"Standard {next(_seq)}",
        base_fare=Decimal('1000'), price_per_km=Decimal('1000'),
        price_per_min_traffic=Decimal('100'), max_speed_kmh_for_traffic=10,
        min_traffic_duration_seconds=30, rounding_unit=50,
        min_fare=Decimal('2000'), is_active=True,
    )
    defaults.update(kwargs)
    return PricingRule.objects.create(**defaults)


def make_client():
    n = next(_seq)
    return User.objects.create_user(
        username=f"client{n}", password='x', phone=f"+2577900{n:04d}",
        role=User.Role.CLIENT,
    )


def make_admin():
    n = next(_seq)
    return User.objects.create_user(
        username=f"admin{n}", password='x', phone=f"+2577800{n:04d}",
        role=User.Role.ADMIN,
    )


def make_driver(lat=PICKUP['lat'], lng=PICKUP['lng'], commission=Decimal('20'),
                ready=True):
    """Chauffeur approuvé, disponible, véhicule actif, contrat signé actif."""
    n = next(_seq)
    user = User.objects.create_user(
        username=f"driver{n}", password='x', phone=f"+2577700{n:04d}",
        role=User.Role.DRIVER, first_name=f"Jean{n}",
    )
    profile = DriverProfile.objects.create(
        user=user, license_number=f"LIC{n}", is_approved=True,
        status=DriverProfile.Status.AVAILABLE if ready else DriverProfile.Status.OFFLINE,
        last_location=make_point(lng, lat), last_location_at=timezone.now(),
    )
    Wallet.objects.create(driver=profile)
    if ready:
        Vehicle.objects.create(
            driver=profile, plate_number=f"B{n:04d}A", brand='Toyota', model='Corolla',
            year=2018, color='Blanc', is_active=True,
        )
        Contract.objects.create(
            driver=profile, commission_percent=commission, content='Contrat',
            signed_at=timezone.now(), is_active=True,
        )
    return profile
