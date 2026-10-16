import hashlib

from django.db import models
from django.utils import timezone

from keeper.models.accounts import DriverProfile, User


class Contract(models.Model):
    """
    Contrat chauffeur ↔ société.
    Règle : on ne modifie JAMAIS un contrat signé.
    Nouveau % = nouveau contrat (l'ancien est archivé via is_active=False).
    """
    driver = models.ForeignKey(
        DriverProfile,
        on_delete=models.CASCADE,
        related_name='contracts',
    )
    commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Pourcentage prélevé par la société (ex: 20.00).",
    )
    content = models.TextField(
        help_text="Texte du contrat affiché au chauffeur.",
    )

    # Signature
    signed_at = models.DateTimeField(null=True, blank=True)
    signature_ip = models.GenericIPAddressField(null=True, blank=True)
    signature_hash = models.CharField(
        max_length=64, blank=True,
        help_text="Hash SHA-256 du contenu signé (preuve d'intégrité).",
    )

    is_active = models.BooleanField(
        default=False,
        help_text="Un seul contrat actif par chauffeur à la fois.",
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_contracts',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'contracts_contract'
        verbose_name = 'Contrat'
        verbose_name_plural = 'Contrats'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['driver', 'is_active']),
        ]

    def __str__(self):
        return f"Contrat #{self.id} — {self.driver.user.username} ({self.commission_percent}%)"

    # ---------- Helpers ----------
    @property
    def is_signed(self):
        return self.signed_at is not None

    def compute_hash(self):
        """Hash SHA-256 du contenu + métadonnées clés."""
        raw = f"{self.id}|{self.driver_id}|{self.commission_percent}|{self.content}"
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def sign(self, ip_address=None):
        """Signe le contrat et calcule le hash d'intégrité."""
        if self.is_signed:
            raise ValueError("Contrat déjà signé.")
        self.signed_at = timezone.now()
        self.signature_ip = ip_address
        self.signature_hash = self.compute_hash()
        self.save(update_fields=['signed_at', 'signature_ip', 'signature_hash', 'updated_at'])