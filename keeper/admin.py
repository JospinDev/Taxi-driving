from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from keeper.models import User, DriverProfile


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