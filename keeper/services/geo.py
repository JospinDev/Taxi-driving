import math
from decimal import Decimal

from django.conf import settings
from django.contrib.gis.geos import Point

EARTH_RADIUS_M = 6_371_008.8


def make_point(lng, lat):
    return Point(float(lng), float(lat), srid=4326)


def haversine_m(p1, p2):
    """Distance grand-cercle en mètres entre deux Point (x=lng, y=lat).
    Calcul Python pur : évite un aller-retour SQL à chaque ping GPS."""
    lng1, lat1, lng2, lat2 = map(math.radians, (p1.x, p1.y, p2.x, p2.y))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def estimate_road_distance_km(pickup, dropoff):
    """Estimation de la distance routière (vol d'oiseau × facteur route)."""
    straight_m = haversine_m(pickup, dropoff)
    km = straight_m / 1000 * settings.DISPATCH['ROAD_FACTOR']
    return Decimal(str(km)).quantize(Decimal('0.001'))


def point_to_dict(point):
    if point is None:
        return None
    return {'lat': point.y, 'lng': point.x}
