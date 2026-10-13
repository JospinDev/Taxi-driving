from django.db import models

from keeper.models.accounts import DriverProfile


class DocumentType(models.Model):
    """
    Type de document configurable par l'admin.
    Ex: 'carte_rose' → 'Carte rose' → obligatoire → avec date d'expiration.
    """
    code = models.SlugField(
        max_length=50, unique=True,
        help_text="Identifiant technique (ex: carte_rose)."
    )
    label = models.CharField(
        max_length=100,
        help_text="Nom affiché (ex: Carte rose)."
    )
    description = models.TextField(blank=True)
    is_required = models.BooleanField(
        default=True,
        help_text="Si coché, le véhicule doit avoir ce doc validé pour être activé."
    )
    requires_expiry = models.BooleanField(
        default=False,
        help_text="Si coché, une date d'expiration est obligatoire à l'upload."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Si décoché, ce type n'est plus proposé à l'upload."
    )
    order = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicles_document_type'
        verbose_name = 'Type de document'
        verbose_name_plural = 'Types de documents'
        ordering = ['order', 'code']

    def __str__(self):
        return f"{self.label} ({self.code})"


class Vehicle(models.Model):
    """Véhicule d'un chauffeur, avec documents en JSONB."""

    driver = models.ForeignKey(
        DriverProfile,
        on_delete=models.CASCADE,
        related_name='vehicles',
    )
    plate_number = models.CharField(max_length=20, unique=True)
    brand = models.CharField(max_length=50)
    model = models.CharField(max_length=50)
    year = models.PositiveIntegerField()
    color = models.CharField(max_length=30)
    seats = models.PositiveSmallIntegerField(default=4)

    # Les clés du JSONB correspondent à DocumentType.code
    documents = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicles_vehicle'
        verbose_name = 'Véhicule'
        verbose_name_plural = 'Véhicules'
        indexes = [
            models.Index(fields=['driver', 'is_active']),
        ]

    def __str__(self):
        return f"{self.plate_number} — {self.brand} {self.model}"

    # ---------- Helpers documents ----------
    def set_document(self, doc_type, data):
        docs = self.documents or {}
        docs[doc_type] = data
        self.documents = docs

    def get_document(self, doc_type):
        return (self.documents or {}).get(doc_type)

    def remove_document(self, doc_type):
        docs = self.documents or {}
        docs.pop(doc_type, None)
        self.documents = docs

    @property
    def required_docs_validated(self):
        """Vérifie que TOUS les DocumentType obligatoires et actifs
        sont présents et validés dans le JSONB."""
        required_codes = DocumentType.objects.filter(
            is_required=True, is_active=True,
        ).values_list('code', flat=True)

        docs = self.documents or {}
        return all(
            docs.get(code, {}).get('is_validated', False)
            for code in required_codes
        )