from rest_framework.permissions import BasePermission


class IsContractOwnerOrAdmin(BasePermission):
    """Le chauffeur ne voit que ses contrats, l'admin voit tout."""
    message = "Ce contrat ne vous appartient pas."

    def has_object_permission(self, request, view, obj):
        if request.user.is_admin_role:
            return True
        if not request.user.is_driver:
            return False
        profile = getattr(request.user, 'driver_profile', None)
        return profile is not None and obj.driver_id == profile.id