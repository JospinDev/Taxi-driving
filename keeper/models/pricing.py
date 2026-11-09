from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator
from decimal import Decimal, ROUND_HALF_UP

from keeper.models.accounts import User


class PricingRule(models.Model):
    """Grille tarifaire."""
    name = models.CharField(max_length=50, unique=True)
    base_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_per_km = models.DecimalField(max_digits=10, decimal_places=2)
    price_per_min_traffic = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    
    # ⬇️ NOUVEAU : Règles de trafic
    max_speed_kmh_for_traffic = models.PositiveIntegerField(
        default=10,
        help_text="Vitesse en dessous de laquelle on considère qu'il y a embouteillage."
    )
    min_traffic_duration_seconds = models.PositiveIntegerField(
        default=30,
        help_text="Durée continue (secondes) sous le seuil pour déclencher la facturation trafic."
    )
    
    # ⬇️ NOUVEAU : Arrondi
    rounding_unit = models.PositiveIntegerField(
        default=50,
        help_text="Arrondir le total au multiple le plus proche (ex: 50 ou 100 BIF).",
        validators=[MinValueValidator(1)]
    )
    
    min_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=False)
    effective_from = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pricing_pricing_rule'
        ordering = ['-effective_from']
        # ⬇️ NOUVEAU : Contrainte unique conditionnelle (un seul actif)
        constraints = [
            models.UniqueConstraint(
                fields=['is_active'],
                condition=models.Q(is_active=True),
                name='unique_active_pricing_rule'
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.price_per_km}/km)"

    # ---------- Logique métier ----------
    def compute_fare(self, distance_km, traffic_minutes=0):
        """
        Calcule le montant brut d'une course.
        1. Base + Km + Trafic
        2. Applique le minimum
        3. Arrondit selon rounding_unit AVANT commission
        """
        # 1. Calcul
        base = self.base_fare
        km_cost = (self.price_per_km * Decimal(str(distance_km))).quantize(Decimal('0.01'))
        traffic_cost = (self.price_per_min_traffic * Decimal(str(traffic_minutes))).quantize(Decimal('0.01'))
        
        subtotal = base + km_cost + traffic_cost
        
        # 2. Minimum
        total_before_rounding = max(subtotal, self.min_fare)
        
        # 3. Arrondi (ex: 2237 -> 2250 si unit=50)
        # On divise par l'unité, on arrondit à l'entier le plus proche, on remultiplie
        rounding_factor = Decimal(str(self.rounding_unit))
        total_rounded = (total_before_rounding / rounding_factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * rounding_factor
        
        return {
            'base_fare': base,
            'km_cost': km_cost,
            'traffic_cost': traffic_cost,
            'subtotal': subtotal,
            'total_rounded': total_rounded  # C'est ce montant qui ira au client
        }