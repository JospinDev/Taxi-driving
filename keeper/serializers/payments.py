from decimal import Decimal

from rest_framework import serializers

from keeper.models import Wallet, WalletTransaction
from keeper.serializers.accounts import UserBasicSerializer


class WalletSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.user.username', read_only=True)

    class Meta:
        model = Wallet
        fields = [
            'id', 'driver', 'driver_name', 'balance', 'currency',
            'total_earned', 'total_commission', 'total_paid_out',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class WalletTransactionSerializer(serializers.ModelSerializer):
    created_by = UserBasicSerializer(read_only=True)

    class Meta:
        model = WalletTransaction
        fields = [
            'id', 'wallet', 'ride', 'type', 'amount', 'balance_after',
            'description', 'reference', 'created_by', 'created_at',
        ]
        read_only_fields = fields


class PayoutSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')


class AdjustmentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2,
        help_text="Signé : positif = crédit, négatif = débit.",
    )
    description = serializers.CharField(max_length=255)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')

    def validate_amount(self, value):
        if value == 0:
            raise serializers.ValidationError("Le montant ne peut pas être nul.")
        return value
