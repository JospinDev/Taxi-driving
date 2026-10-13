from rest_framework.permissions import BasePermission, SAFE_METHODS


class IsClient(BasePermission):
    """Autorise uniquement les clients."""
    message = "Réservé aux clients."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_client


class IsDriver(BasePermission):
    """Autorise uniquement les chauffeurs."""
    message = "Réservé aux chauffeurs."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_driver


class IsDriverApproved(BasePermission):
    """Autorise uniquement les chauffeurs approuvés par l'admin."""
    message = "Votre compte chauffeur n'est pas encore validé."

    def has_permission(self, request, view):
        if not (request.user.is_authenticated and request.user.is_driver):
            return False
        profile = getattr(request.user, 'driver_profile', None)
        return bool(profile and profile.is_approved)


class IsAdminRole(BasePermission):
    """Autorise uniquement le rôle admin (ou superuser)."""
    message = "Réservé aux administrateurs."

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_admin_role


class IsSelfOrAdmin(BasePermission):
    """Lecture/modif de son propre profil, ou admin."""
    message = "Vous ne pouvez modifier que votre propre compte."

    def has_object_permission(self, request, view, obj):
        if request.user.is_admin_role:
            return True
        # obj peut être un User ou un DriverProfile
        target_user = obj.user if hasattr(obj, 'user') else obj
        return target_user == request.user