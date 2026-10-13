from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from keeper.models import User, DriverProfile, Vehicle
from keeper.models.vehicles import DocumentType


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