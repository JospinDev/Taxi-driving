from django.contrib.gis.db import models as gis_models
from django.db import models
from django.db.models import Q
from django.utils import timezone

from keeper.models.accounts import DriverProfile, User
from keeper.models.contracts import Contract
from keeper.models.pricing import PricingRule
from keeper.models.vehicles import Vehicle


class Ride(models.Model):
    """
    Course client.
    Cycle : searching → accepted → arrived → in_progress → completed
            (+ cancelled / no_driver)
    Les montants finaux sont figés à la clôture (jamais recalculés ensuite).
    """

    class Status(models.TextChoices):
        SEARCHING = 'searching', 'Recherche de chauffeur'
        ACCEPTED = 'accepted', 'Chauffeur en route'
        ARRIVED = 'arrived', 'Chauffeur arrivé'
        IN_PROGRESS = 'in_progress', 'En course'
        COMPLETED = 'completed', 'Terminée'
        CANCELLED = 'cancelled', 'Annulée'
        NO_DRIVER = 'no_driver', 'Aucun chauffeur disponible'

    ACTIVE_STATUSES = (
        Status.SEARCHING, Status.ACCEPTED, Status.ARRIVED, Status.IN_PROGRESS,
    )
    DRIVER_ACTIVE_STATUSES = (
        Status.ACCEPTED, Status.ARRIVED, Status.IN_PROGRESS,
    )

    class PaymentMethod(models.TextChoices):
        CASH = 'cash', 'Espèces'
        MOBILE_MONEY = 'mobile_money', 'Mobile money'

    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'En attente'
        PAID = 'paid', 'Payé'

    class CancelledBy(models.TextChoices):
        CLIENT = 'client', 'Client'
        DRIVER = 'driver', 'Chauffeur'
        ADMIN = 'admin', 'Administrateur'
        SYSTEM = 'system', 'Système'

    # ---------- Acteurs ----------
    client = models.ForeignKey(User, on_delete=models.PROTECT, related_name='rides')
    driver = models.ForeignKey(
        DriverProfile, on_delete=models.PROTECT,
        null=True, blank=True, related_name='rides',
    )
    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.PROTECT,
        null=True, blank=True, related_name='rides',
    )
    # Snapshots : le tarif et la commission appliqués sont ceux du moment
    contract = models.ForeignKey(
        Contract, on_delete=models.PROTECT,
        null=True, blank=True, related_name='rides',
    )
    pricing_rule = models.ForeignKey(
        PricingRule, on_delete=models.PROTECT, related_name='rides',
    )

    status = models.CharField(
        max_length=15, choices=Status.choices,
        default=Status.SEARCHING, db_index=True,
    )

    # ---------- Trajet ----------
    pickup_location = gis_models.PointField(geography=True)
    pickup_address = models.CharField(max_length=255, blank=True)
    dropoff_location = gis_models.PointField(geography=True)
    dropoff_address = models.CharField(max_length=255, blank=True)

    # ---------- Estimation (au moment de la commande) ----------
    estimated_distance_km = models.DecimalField(max_digits=8, decimal_places=3)
    estimated_fare = models.DecimalField(max_digits=10, decimal_places=2)

    # ---------- Dispatch ----------
    dispatch_round = models.PositiveSmallIntegerField(
        default=0, help_text="Incrémenté à chaque relance (ex: chauffeur qui annule).",
    )
    dispatch_wave = models.PositiveSmallIntegerField(default=0)

    # ---------- Agrégats de tracking (mis à jour à chaque ping, O(1)) ----------
    distance_m = models.FloatField(default=0)
    traffic_seconds = models.FloatField(default=0)
    slow_streak_seconds = models.FloatField(
        default=0, help_text="Durée continue actuelle sous le seuil de vitesse.",
    )
    last_track_location = gis_models.PointField(geography=True, null=True, blank=True, spatial_index=False)
    last_track_at = models.DateTimeField(null=True, blank=True)
    track_points_count = models.PositiveIntegerField(default=0)

    # ---------- Montants finaux (figés à la clôture) ----------
    actual_distance_km = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    traffic_minutes = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    distance_fallback = models.BooleanField(
        default=False, help_text="True si le GPS a manqué et que l'estimation a été facturée.",
    )
    fare_base = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fare_km = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fare_traffic = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fare_subtotal = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    fare_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    commission_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    driver_earning = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # ---------- Paiement ----------
    payment_method = models.CharField(
        max_length=15, choices=PaymentMethod.choices, default=PaymentMethod.CASH,
    )
    payment_status = models.CharField(
        max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING,
    )
    payment_reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    # ---------- Note ----------
    client_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    client_comment = models.TextField(blank=True)

    # ---------- Annulation ----------
    cancelled_by = models.CharField(max_length=10, choices=CancelledBy.choices, blank=True)
    cancel_reason = models.CharField(max_length=255, blank=True)

    # ---------- Horodatage ----------
    requested_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rides_ride'
        verbose_name = 'Course'
        verbose_name_plural = 'Courses'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['client', '-requested_at']),
            models.Index(fields=['driver', '-requested_at']),
            models.Index(fields=['status', '-requested_at']),
        ]
        constraints = [
            # Garde-fous en base contre les doubles commandes / doubles acceptations
            models.UniqueConstraint(
                fields=['client'],
                condition=Q(status__in=['searching', 'accepted', 'arrived', 'in_progress']),
                name='unique_active_ride_per_client',
            ),
            models.UniqueConstraint(
                fields=['driver'],
                condition=Q(status__in=['accepted', 'arrived', 'in_progress']),
                name='unique_active_ride_per_driver',
            ),
            models.CheckConstraint(
                condition=Q(client_rating__isnull=True)
                | Q(client_rating__gte=1, client_rating__lte=5),
                name='ride_rating_between_1_and_5',
            ),
        ]

    def __str__(self):
        return f"Course #{self.pk} [{self.status}]"

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES

    @property
    def duration_seconds(self):
        if self.started_at and self.completed_at:
            return int((self.completed_at - self.started_at).total_seconds())
        return None


class RideOffer(models.Model):
    """Proposition d'une course à un chauffeur (une ligne par chauffeur notifié)."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'En attente'
        ACCEPTED = 'accepted', 'Acceptée'
        REJECTED = 'rejected', 'Refusée'
        EXPIRED = 'expired', 'Expirée'
        CANCELLED = 'cancelled', 'Annulée'

    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='offers')
    driver = models.ForeignKey(DriverProfile, on_delete=models.CASCADE, related_name='ride_offers')
    wave = models.PositiveSmallIntegerField(default=1)
    distance_m = models.FloatField(help_text="Distance chauffeur → point de prise en charge.")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'rides_ride_offer'
        verbose_name = 'Offre de course'
        verbose_name_plural = 'Offres de course'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['ride', 'driver'], name='unique_offer_per_ride_driver'),
        ]
        indexes = [
            models.Index(fields=['driver', 'status', '-created_at']),
            models.Index(fields=['ride', 'status']),
        ]

    def __str__(self):
        return f"Offre ride#{self.ride_id} → driver#{self.driver_id} [{self.status}]"


class RideTrackPoint(models.Model):
    """Point GPS enregistré pendant la course (in_progress uniquement)."""

    id = models.BigAutoField(primary_key=True)
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name='track_points')
    # Pas d'index spatial : jamais filtré géographiquement, on économise l'écriture
    location = gis_models.PointField(geography=True, spatial_index=False)
    speed_kmh = models.FloatField(default=0)
    accuracy_m = models.FloatField(null=True, blank=True)
    heading = models.FloatField(null=True, blank=True)
    is_traffic = models.BooleanField(default=False)
    recorded_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rides_track_point'
        verbose_name = 'Point GPS'
        verbose_name_plural = 'Points GPS'
        ordering = ['recorded_at']
        indexes = [
            models.Index(fields=['ride', 'recorded_at']),
        ]
