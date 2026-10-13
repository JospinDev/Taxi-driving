from django.contrib.auth.models import AbstractUser
from django.contrib.gis.db import models as gis_models
from django.core.validators import RegexValidator
from django.utils import timezone
from django.db import models


phone_validator = RegexValidator(
    regex=r'^\+?[0-9]{8,15}$',
    message="Le numéro doit être au format international (+257XXXXXXXX)."
)


class User(AbstractUser):
    """Utilisateur custom : client, chauffeur ou admin."""

    class Role(models.TextChoices):
        CLIENT = 'client', 'Client'
        DRIVER = 'driver', 'Chauffeur'
        ADMIN = 'admin', 'Administrateur'

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.CLIENT,
        db_index=True,
    )
    phone = models.CharField(
        max_length=20,
        unique=True,
        validators=[phone_validator],
    )
    whatsapp = models.CharField(max_length=20, blank=True)
    is_verified = models.BooleanField(default=False)
    fcm_token = models.TextField(blank=True, help_text="Token Firebase pour les push")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accounts_user'
        verbose_name = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'

    def __str__(self):
        return f"{self.username} ({self.role})"

    # Helpers pratiques
    @property
    def is_client(self):
        return self.role == self.Role.CLIENT

    @property
    def is_driver(self):
        return self.role == self.Role.DRIVER

    @property
    def is_admin_role(self):
        return self.role == self.Role.ADMIN or self.is_superuser


class DriverProfile(models.Model):
    """Profil étendu du chauffeur."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'En attente de validation'
        AVAILABLE = 'available', 'Disponible'
        BUSY = 'busy', 'En course'
        OFFLINE = 'offline', 'Hors ligne'

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='driver_profile',
        limit_choices_to={'role': User.Role.DRIVER},
    )
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    license_number = models.CharField(max_length=50, unique=True)

    # Géolocalisation (PostGIS)
    last_location = gis_models.PointField(
        null=True, blank=True,
        geography=True,
        help_text="Dernière position connue (lng, lat)"
    )
    last_location_at = models.DateTimeField(null=True, blank=True)

    rating = models.DecimalField(max_digits=3, decimal_places=2, default=5.00)
    total_rides = models.PositiveIntegerField(default=0)

    is_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'accounts_driver_profile'
        verbose_name = 'Profil chauffeur'
        verbose_name_plural = 'Profils chauffeurs'
        indexes = [
            models.Index(fields=['status', 'is_approved']),
        ]

    def __str__(self):
        return f"Driver: {self.user.username} [{self.status}]"

    def approve(self):
        self.is_approved = True
        self.approved_at = timezone.now()
        self.status = self.Status.OFFLINE
        self.save(update_fields=['is_approved', 'approved_at', 'status'])