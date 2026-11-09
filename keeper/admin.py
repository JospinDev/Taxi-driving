from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from keeper.models import (
    Contract, DocumentType, DriverProfile, Notification, PricingRule, Ride,
    RideOffer, User, Vehicle, Wallet, WalletTransaction,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('id', 'username', 'email', 'phone', 'role', 'is_verified', 'is_active')
    list_filter = ('role', 'is_verified', 'is_active')
    search_fields = ('username', 'email', 'phone', 'first_name', 'last_name')
    ordering = ('-created_at',)

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Keeper', {'fields': ('role', 'phone', 'whatsapp', 'is_verified', 'fcm_token')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Keeper', {'fields': ('role', 'phone', 'whatsapp')}),
    )


@admin.register(DriverProfile)
class DriverProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'license_number', 'status', 'is_approved', 'rating', 'total_rides')
    list_filter = ('status', 'is_approved')
    search_fields = ('user__username', 'user__phone', 'license_number')
    readonly_fields = ('approved_at', 'last_location_at', 'created_at', 'updated_at')

@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ('id', 'code', 'label', 'is_required', 'requires_expiry',
                    'is_active', 'order')
    list_filter = ('is_required', 'requires_expiry', 'is_active')
    search_fields = ('code', 'label')
    ordering = ('order', 'code')
    
@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('id', 'plate_number', 'brand', 'model', 'year',
                    'driver', 'is_active', 'created_at')
    list_filter = ('is_active', 'brand', 'year')
    search_fields = ('plate_number', 'brand', 'model',
                     'driver__user__username', 'driver__user__phone')
    readonly_fields = ('created_at', 'updated_at', 'documents')

@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = ('id', 'driver', 'commission_percent', 'is_signed',
                    'is_active', 'signed_at', 'created_at')
    list_filter = ('is_active', 'signed_at')
    search_fields = ('driver__user__username', 'driver__user__phone')
    readonly_fields = ('signed_at', 'signature_ip', 'signature_hash',
                       'created_at', 'updated_at')


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'price_per_km', 'price_per_min_traffic',
                    'base_fare', 'min_fare', 'rounding_unit', 'is_active', 'effective_from')
    list_filter = ('is_active',)
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')


class RideOfferInline(admin.TabularInline):
    model = RideOffer
    extra = 0
    can_delete = False
    readonly_fields = ('driver', 'wave', 'distance_m', 'status', 'created_at', 'responded_at')


@admin.register(Ride)
class RideAdmin(admin.ModelAdmin):
    list_display = ('id', 'client', 'driver', 'status', 'payment_method', 'payment_status',
                    'estimated_fare', 'fare_total', 'requested_at')
    list_filter = ('status', 'payment_method', 'payment_status', 'cancelled_by')
    search_fields = ('id', 'client__username', 'client__phone', 'driver__user__username')
    list_select_related = ('client', 'driver__user')
    raw_id_fields = ('client', 'driver', 'vehicle', 'contract', 'pricing_rule')
    date_hierarchy = 'requested_at'
    inlines = [RideOfferInline]
    # Les montants et le cycle de vie passent par les services, jamais à la main
    readonly_fields = [f.name for f in Ride._meta.fields]


class ReadOnlyAdmin(admin.ModelAdmin):
    """Journal : consultation uniquement (les écritures passent par les services)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Wallet)
class WalletAdmin(ReadOnlyAdmin):
    list_display = ('id', 'driver', 'balance', 'total_earned', 'total_commission',
                    'total_paid_out', 'updated_at')
    search_fields = ('driver__user__username', 'driver__user__phone')
    list_select_related = ('driver__user',)


@admin.register(WalletTransaction)
class WalletTransactionAdmin(ReadOnlyAdmin):
    list_display = ('id', 'wallet', 'type', 'amount', 'balance_after', 'ride', 'reference', 'created_at')
    list_filter = ('type',)
    search_fields = ('wallet__driver__user__username', 'reference', 'description')
    list_select_related = ('wallet__driver__user',)
    date_hierarchy = 'created_at'


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdmin):
    list_display = ('id', 'user', 'channel', 'event', 'status', 'attempts', 'created_at', 'sent_at')
    list_filter = ('channel', 'status', 'event')
    search_fields = ('user__username', 'user__phone', 'body')
    list_select_related = ('user',)
    date_hierarchy = 'created_at'
