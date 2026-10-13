import os
from django.utils import timezone
from rest_framework import serializers

from keeper.models import DocumentType, Vehicle
from keeper.serializers.common import EmptySerializer


# ============================================================
# VÉHICULE — LECTURE
# ============================================================
class VehicleSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.user.username', read_only=True)
    required_docs_validated = serializers.BooleanField(read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            'id', 'driver', 'driver_name',
            'plate_number', 'brand', 'model', 'year', 'color', 'seats',
            'documents', 'is_active',
            'required_docs_validated',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'driver', 'documents', 'is_active',
            'required_docs_validated',
            'created_at', 'updated_at',
        ]


# ============================================================
# VÉHICULE — CRÉATION (chauffeur)
# ============================================================
class VehicleCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ['plate_number', 'brand', 'model', 'year', 'color', 'seats']

    def validate_plate_number(self, value):
        if Vehicle.objects.filter(plate_number=value).exists():
            raise serializers.ValidationError("Cette plaque est déjà enregistrée.")
        return value

    def validate_year(self, value):
        current = timezone.now().year
        if value < 1980 or value > current + 1:
            raise serializers.ValidationError(
                f"Année invalide (doit être entre 1980 et {current + 1})."
            )
        return value


# ============================================================
# VÉHICULE — MISE À JOUR
# ============================================================
class VehicleUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ['brand', 'model', 'year', 'color', 'seats']

    def validate_year(self, value):
        current = timezone.now().year
        if value < 1980 or value > current + 1:
            raise serializers.ValidationError(
                f"Année invalide (doit être entre 1980 et {current + 1})."
            )
        return value


# ============================================================
# DOCUMENT — UPLOAD
# ============================================================
class DocumentUploadSerializer(serializers.Serializer):
    """
    Upload d'un document pour un véhicule.
    Requête : multipart/form-data
    Champs  : doc_type, file, expires_at (optionnel selon le type)
    """
    doc_type = serializers.SlugField(max_length=50)
    file = serializers.FileField(
        max_length=255,
        help_text="Fichier PDF ou image (max 5 Mo).",
    )
    expires_at = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Date d'expiration (YYYY-MM-DD). Obligatoire si le type l'exige.",
    )

    def validate_doc_type(self, value):
        if not DocumentType.objects.filter(code=value, is_active=True).exists():
            raise serializers.ValidationError(
                f"Type de document inconnu ou désactivé : {value}"
            )
        return value

    def validate_file(self, value):
        max_size = 5 * 1024 * 1024   # 5 Mo
        if value.size > max_size:
            raise serializers.ValidationError("Fichier trop volumineux (max 5 Mo).")

        allowed_ext = ['.pdf', '.jpg', '.jpeg', '.png']
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in allowed_ext:
            raise serializers.ValidationError(
                f"Format non supporté. Autorisés : {', '.join(allowed_ext)}"
            )
        return value

    def validate(self, attrs):
        doc_type_code = attrs['doc_type']
        doc_type = DocumentType.objects.get(code=doc_type_code)
        expires_at = attrs.get('expires_at')

        if doc_type.requires_expiry:
            if not expires_at:
                raise serializers.ValidationError({
                    'expires_at': f"Le document '{doc_type.label}' doit avoir une date d'expiration."
                })
            if expires_at <= timezone.now().date():
                raise serializers.ValidationError({
                    'expires_at': "La date d'expiration doit être dans le futur."
                })
        else:
            if expires_at:
                raise serializers.ValidationError({
                    'expires_at': f"Le document '{doc_type.label}' n'accepte pas de date d'expiration."
                })

        return attrs


# ============================================================
# DOCUMENT — VALIDATION (admin)
# ============================================================
class DocumentValidateSerializer(serializers.Serializer):
    """
    Validation ou refus d'un document par un admin.
    """
    doc_type = serializers.SlugField(max_length=50)
    is_validated = serializers.BooleanField()
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate_doc_type(self, value):
        if not DocumentType.objects.filter(code=value).exists():
            raise serializers.ValidationError(
                f"Type de document inconnu : {value}"
            )
        return value


# ============================================================
# DOCUMENT TYPE — CRUD (admin)
# ============================================================
class DocumentTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentType
        fields = [
            'id', 'code', 'label', 'description',
            'is_required', 'requires_expiry', 'is_active', 'order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_code(self, value):
        qs = DocumentType.objects.filter(code=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ce code existe déjà.")
        return value