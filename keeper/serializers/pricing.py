from rest_framework import serializers

from keeper.models import PricingRule
from keeper.serializers.accounts import UserBasicSerializer


# ============================================================
# LECTURE
# ============================================================
class PricingRuleSerializer(serializers.ModelSerializer):
    created_by = UserBasicSerializer(read_only=True)
    class Meta:
        model = PricingRule
        fields = [
            'id', 'name',
            'base_fare', 'price_per_km', 'price_per_min_traffic',
            'max_speed_kmh_for_traffic', 'min_traffic_duration_seconds',
            'rounding_unit', 'min_fare',
            'is_active', 'effective_from',
            'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'is_active', 'created_by',
            'created_at', 'updated_at',
        ]


# ============================================================
# CRÉATION / MISE À JOUR (ADMIN)
# ============================================================
class PricingRuleWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingRule
        fields = [
            'name', 'base_fare', 'price_per_km', 'price_per_min_traffic',
            'max_speed_kmh_for_traffic', 'min_traffic_duration_seconds',
            'rounding_unit', 'min_fare', 'effective_from',
        ]

    def validate_price_per_km(self, value):
        if value <= 0:
            raise serializers.ValidationError("Le prix par km doit être positif.")
        return value

    def validate_price_per_min_traffic(self, value):
        if value < 0:
            raise serializers.ValidationError("Le prix par minute doit être positif ou nul.")
        return value

    def validate_base_fare(self, value):
        if value < 0:
            raise serializers.ValidationError("Le tarif de base doit être positif ou nul.")
        return value

    def validate_min_fare(self, value):
        if value < 0:
            raise serializers.ValidationError("Le tarif minimum doit être positif ou nul.")
        return value