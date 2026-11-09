from rest_framework import status
from rest_framework.exceptions import APIException


class BusinessError(APIException):
    """Règle métier violée → 400. Levée par les services, rendue telle quelle par DRF."""
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Opération impossible."
    default_code = 'business_error'


class ConflictError(BusinessError):
    """Conflit de concurrence / état déjà changé → 409."""
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Ressource déjà prise ou modifiée par une autre requête."
    default_code = 'conflict'
