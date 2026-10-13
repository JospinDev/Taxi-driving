from rest_framework.permissions import BasePermission


class IsVehicleOwner(BasePermission):
    """Le chauffeur ne touche qu'à ses propres véhicules."""
    message = "Ce véhicule ne vous appartient pas."

    def has_object_permission(self, request, view, obj):
        if request.user.is_admin_role:
            return True
        return (
            request.user.is_driver
            and hasattr(request.user, 'driver_profile')
            and obj.driver_id == request.user.driver_profile.id
        )


class IsVehicleOwnerOrAdmin(BasePermission):
    """Lecture : propriétaire OU admin. Modification : idem."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_admin_role:
            return True
        if not request.user.is_driver:
            return False
        profile = getattr(request.user, 'driver_profile', None)
        return profile is not None and obj.driver_id == profile.id