from rest_framework import serializers

from keeper.models import Contract


# ============================================================
# LECTURE
# ============================================================
class ContractSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.user.username', read_only=True)
    is_signed = serializers.BooleanField(read_only=True)

    class Meta:
        model = Contract
        fields = [
            'id', 'driver', 'driver_name',
            'commission_percent', 'content',
            'signed_at', 'signature_ip', 'signature_hash',
            'is_active', 'is_signed',
            'created_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'driver', 'signed_at', 'signature_ip', 'signature_hash',
            'is_active', 'is_signed', 'created_by',
            'created_at', 'updated_at',
        ]


# ============================================================
# CRÉATION (ADMIN)
# ============================================================
class ContractCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contract
        fields = ['driver', 'commission_percent', 'content']

    def validate_commission_percent(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError("Le pourcentage doit être entre 0 et 100.")
        return value

    def validate(self, attrs):
        driver = attrs['driver']
        # Refuser si un contrat non signé existe déjà
        if Contract.objects.filter(driver=driver, signed_at__isnull=True).exists():
            raise serializers.ValidationError({
                'driver': "Ce chauffeur a déjà un contrat en attente de signature."
            })
        return attrs


# ============================================================
# MISE À JOUR (ADMIN, seulement si non signé)
# ============================================================
class ContractUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contract
        fields = ['commission_percent', 'content']

    def validate_commission_percent(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError("Le pourcentage doit être entre 0 et 100.")
        return value

    def update(self, instance, validated_data):
        if instance.is_signed:
            raise serializers.ValidationError(
                "Impossible de modifier un contrat déjà signé. Créez-en un nouveau."
            )
        return super().update(instance, validated_data)