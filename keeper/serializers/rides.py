from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from keeper.models import Ride, RideOffer, RideTrackPoint
from keeper.services.geo import make_point, point_to_dict


# ============================================================
# ENTRÉES
# ============================================================
class LatLngSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        return make_point(values['lng'], values['lat'])


class NearbyDriversQuerySerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)
    radius_m = serializers.IntegerField(required=False, default=5000, min_value=100, max_value=15000)


class NearbyDriverSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    lat = serializers.FloatField()
    lng = serializers.FloatField()
    distance_m = serializers.IntegerField()


class RideEstimateSerializer(serializers.Serializer):
    pickup = LatLngSerializer()
    dropoff = LatLngSerializer()


class RideCreateSerializer(RideEstimateSerializer):
    pickup_address = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    dropoff_address = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    payment_method = serializers.ChoiceField(
        choices=Ride.PaymentMethod.choices, default=Ride.PaymentMethod.CASH,
    )


class RideCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class RideRateSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)


class ConfirmPaymentSerializer(serializers.Serializer):
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')


class TrackPointInputSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)
    recorded_at = serializers.DateTimeField(required=False, default=timezone.now)
    accuracy = serializers.FloatField(required=False, allow_null=True, min_value=0)
    heading = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=360)


class TrackBatchSerializer(serializers.Serializer):
    points = TrackPointInputSerializer(
        many=True, allow_empty=False,
        max_length=settings.TRACKING['MAX_POINTS_PER_BATCH'],
    )


# ============================================================
# SORTIES
# ============================================================
class RideEstimateResultSerializer(serializers.Serializer):
    distance_km = serializers.DecimalField(max_digits=8, decimal_places=3)
    fare = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
    pricing_rule = serializers.CharField()


class RideSerializer(serializers.ModelSerializer):
    pickup = serializers.SerializerMethodField()
    dropoff = serializers.SerializerMethodField()
    client = serializers.SerializerMethodField()
    driver = serializers.SerializerMethodField()
    vehicle = serializers.SerializerMethodField()
    duration_seconds = serializers.IntegerField(read_only=True)
    live_distance_km = serializers.SerializerMethodField()
    live_traffic_minutes = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = [
            'id', 'status',
            'client', 'driver', 'vehicle',
            'pickup', 'pickup_address', 'dropoff', 'dropoff_address',
            'estimated_distance_km', 'estimated_fare',
            'live_distance_km', 'live_traffic_minutes',
            'actual_distance_km', 'traffic_minutes', 'distance_fallback', 'duration_seconds',
            'fare_base', 'fare_km', 'fare_traffic', 'fare_subtotal', 'fare_total',
            'commission_percent', 'commission_amount', 'driver_earning',
            'payment_method', 'payment_status', 'payment_reference', 'paid_at',
            'client_rating', 'client_comment',
            'cancelled_by', 'cancel_reason',
            'requested_at', 'accepted_at', 'arrived_at', 'started_at',
            'completed_at', 'cancelled_at',
        ]
        read_only_fields = fields

    def get_pickup(self, obj) -> dict:
        return point_to_dict(obj.pickup_location)

    def get_dropoff(self, obj) -> dict:
        return point_to_dict(obj.dropoff_location)

    def get_client(self, obj) -> dict:
        c = obj.client
        return {'id': c.pk, 'name': c.get_full_name() or c.username, 'phone': c.phone}

    def get_driver(self, obj) -> dict | None:
        if not obj.driver_id:
            return None
        d = obj.driver
        return {
            'id': d.pk,
            'name': d.user.get_full_name() or d.user.username,
            'phone': d.user.phone,
            'rating': d.rating,
            'location': point_to_dict(d.last_location) if obj.is_active else None,
        }

    def get_vehicle(self, obj) -> dict | None:
        if not obj.vehicle_id:
            return None
        v = obj.vehicle
        return {
            'id': v.pk, 'plate_number': v.plate_number, 'brand': v.brand,
            'model': v.model, 'color': v.color,
        }

    def get_live_distance_km(self, obj) -> float:
        return round(obj.distance_m / 1000, 3)

    def get_live_traffic_minutes(self, obj) -> float:
        return round(obj.traffic_seconds / 60, 2)

    def to_representation(self, obj):
        data = super().to_representation(obj)
        # La commission ne regarde pas le client
        request = self.context.get('request')
        if request and request.user.pk == obj.client_id and not request.user.is_admin_role:
            for key in ('commission_percent', 'commission_amount', 'driver_earning'):
                data.pop(key, None)
        return data


class RideOfferSerializer(serializers.ModelSerializer):
    """Offre vue par le chauffeur (le client n'est révélé qu'après acceptation)."""
    pickup = serializers.SerializerMethodField()
    dropoff = serializers.SerializerMethodField()
    pickup_address = serializers.CharField(source='ride.pickup_address')
    dropoff_address = serializers.CharField(source='ride.dropoff_address')
    estimated_fare = serializers.DecimalField(source='ride.estimated_fare', max_digits=10, decimal_places=2)
    estimated_distance_km = serializers.DecimalField(
        source='ride.estimated_distance_km', max_digits=8, decimal_places=3,
    )
    payment_method = serializers.CharField(source='ride.payment_method')

    class Meta:
        model = RideOffer
        fields = [
            'id', 'ride', 'status', 'wave', 'distance_m',
            'pickup', 'pickup_address', 'dropoff', 'dropoff_address',
            'estimated_fare', 'estimated_distance_km', 'payment_method',
            'created_at',
        ]
        read_only_fields = fields

    def get_pickup(self, obj) -> dict:
        return point_to_dict(obj.ride.pickup_location)

    def get_dropoff(self, obj) -> dict:
        return point_to_dict(obj.ride.dropoff_location)


class RideTrackPointSerializer(serializers.ModelSerializer):
    lat = serializers.SerializerMethodField()
    lng = serializers.SerializerMethodField()

    class Meta:
        model = RideTrackPoint
        fields = ['lat', 'lng', 'speed_kmh', 'heading', 'is_traffic', 'recorded_at']
        read_only_fields = fields

    def get_lat(self, obj) -> float:
        return obj.location.y

    def get_lng(self, obj) -> float:
        return obj.location.x
