from django.conf import settings
from django.db import models
from django.db.models import Q

from keeper.models.accounts import DriverProfile, User
from keeper.models.rides import Ride


class Wallet(models.Model):
    """
    Portefeuille chauffeur.
    balance > 0 : la société doit de l'argent au chauffeur.
    balance < 0 : le chauffeur doit des commissions (courses payées en espèces).
    Ne JAMAIS modifier balance hors de keeper.services.payments (verrou + journal).
    """
    driver = models.OneToOneField(DriverProfile, on_delete=models.PROTECT, related_name='wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_earned = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_commission = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_paid_out = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default=settings.CURRENCY)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'payments_wallet'
        verbose_name = 'Portefeuille'
        verbose_name_plural = 'Portefeuilles'

    def __str__(self):
        return f"Wallet {self.driver.user.username} : {self.balance} {self.currency}"


class WalletTransaction(models.Model):
    """Journal immuable des mouvements du portefeuille (montant signé)."""

    class Type(models.TextChoices):
        RIDE_EARNING = 'ride_earning', 'Gain de course (payée via la société)'
        COMMISSION = 'commission', 'Commission due (course payée en espèces)'
        PAYOUT = 'payout', 'Versement au chauffeur'
        ADJUSTMENT = 'adjustment', 'Ajustement admin'

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name='transactions')
    ride = models.ForeignKey(
        Ride, on_delete=models.PROTECT, null=True, blank=True,
        related_name='wallet_transactions',
    )
    type = models.CharField(max_length=15, choices=Type.choices, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='wallet_transactions_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'payments_wallet_transaction'
        verbose_name = 'Transaction portefeuille'
        verbose_name_plural = 'Transactions portefeuille'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['wallet', '-created_at']),
        ]
        constraints = [
            # Idempotence : une course ne peut être réglée qu'une fois par type
            models.UniqueConstraint(
                fields=['ride', 'type'],
                condition=Q(ride__isnull=False),
                name='unique_wallet_tx_per_ride_type',
            ),
        ]

    def __str__(self):
        return f"{self.type} {self.amount} (wallet#{self.wallet_id})"
