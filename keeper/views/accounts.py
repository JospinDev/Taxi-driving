from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from keeper.models import DriverProfile, User
from keeper.permissions.accounts import IsAdminRole, IsDriver, IsDriverApproved
from keeper.serializers.accounts import (
    ClientRegisterSerializer, DriverLocationSerializer, DriverProfileSerializer,
    DriverRegisterSerializer, DriverStatusSerializer, LoginSerializer,
    UpdateMeSerializer, UserSerializer,
)


# ============================================================
# AUTH VIEWSET — register client / register driver / login
# ============================================================
class AuthViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    """
    - POST /accounts/auth/register-client/
    - POST /accounts/auth/register-driver/
    - POST /accounts/auth/login/
    """
    queryset = User.objects.none()
    serializer_class = ClientRegisterSerializer
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == 'register_driver':
            return DriverRegisterSerializer
        if self.action == 'login':
            return LoginSerializer
        return ClientRegisterSerializer

    @action(detail=False, methods=['post'], url_path='register-client')
    def register_client(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='register-driver')
    def register_driver(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='login')
    def login(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)


# ============================================================
# ME VIEWSET — profil de l'utilisateur connecté
# ============================================================
class MeViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    - GET   /accounts/me/
    - PATCH /accounts/me/
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(id=self.request.user.id)

    def get_object(self):
        return self.request.user

    def get_serializer_class(self):
        if self.action in ('update', 'partial_update'):
            return UpdateMeSerializer
        return UserSerializer


# ============================================================
# DRIVER VIEWSET
# ============================================================
class DriverProfileViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Routes :
      GET    /accounts/drivers/                    LIST (admin)
      GET    /accounts/drivers/{id}/               RETRIEVE (admin ou soi)
      PATCH  /accounts/drivers/{id}/               UPDATE (admin)
      GET    /accounts/drivers/me/                 RETRIEVE (chauffeur connecté)
      PATCH  /accounts/drivers/me/location/        maj GPS
      PATCH  /accounts/drivers/me/status/          dispo / hors ligne
      POST   /accounts/drivers/{id}/approve/       ADMIN
      POST   /accounts/drivers/{id}/suspend/       ADMIN
    """
    queryset = DriverProfile.objects.select_related('user').all()
    serializer_class = DriverProfileSerializer
    permission_classes = [IsAuthenticated]

    # ---------- Filtres inline ----------
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        'status': ['exact'],
        'is_approved': ['exact'],
        'rating': ['exact', 'gte', 'lte'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = [
        'user__username', 'user__phone', 'license_number',
        'user__first_name', 'user__last_name',
    ]
    ordering_fields = ['rating', 'total_rides', 'created_at']
    ordering = ['-created_at']

    # ---------- Permissions dynamiques ----------
    def get_permissions(self):
        if self.action in ('approve', 'suspend', 'update', 'partial_update'):
            return [IsAdminRole()]
        if self.action in ('me', 'location', 'status'):
            return [IsDriver()]
        return super().get_permissions()

    # ---------- Queryset dynamique ----------
    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or user.is_admin_role:
            return DriverProfile.objects.select_related('user').all().order_by('-id')

        if user.is_driver:
            return DriverProfile.objects.select_related('user').filter(user=user)

        return DriverProfile.objects.none()

    # ---------- Actions ----------
    @action(detail=False, methods=['get'], url_path='me')
    def me(self, request):
        profile = getattr(request.user, 'driver_profile', None)
        if not profile:
            return Response(
                {'detail': "Profil chauffeur introuvable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(self.get_serializer(profile).data)

    @action(
        detail=False, methods=['patch'], url_path='me/location',
        permission_classes=[IsDriverApproved],
        serializer_class=DriverLocationSerializer,
    )
    def location(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = request.user.driver_profile
        profile.last_location = Point(
            serializer.validated_data['lng'],
            serializer.validated_data['lat'],
            srid=4326,
        )
        profile.last_location_at = timezone.now()
        profile.save(update_fields=['last_location', 'last_location_at'])
        return Response({'detail': "Position mise à jour."})

    @action(
        detail=False, methods=['patch'], url_path='me/status',
        permission_classes=[IsDriverApproved],
        serializer_class=DriverStatusSerializer,
    )
    def status(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = request.user.driver_profile
        if profile.status == DriverProfile.Status.BUSY:
            return Response(
                {'detail': "Impossible de changer le statut pendant une course."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile.status = serializer.validated_data['status']
        profile.save(update_fields=['status'])
        return Response({'status': profile.status})

    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        profile = self.get_object()
        if profile.is_approved:
            return Response(
                {'detail': "Déjà approuvé."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile.approve()
        return Response(self.get_serializer(profile).data)

    @action(detail=True, methods=['post'], url_path='suspend')
    def suspend(self, request, pk=None):
        profile = self.get_object()
        profile.is_approved = False
        profile.status = DriverProfile.Status.PENDING
        profile.save(update_fields=['is_approved', 'status'])
        return Response(self.get_serializer(profile).data)


# ============================================================
# USER VIEWSET — admin uniquement
# ============================================================
class UserViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    - GET    /accounts/users/
    - GET    /accounts/users/{id}/
    - PATCH  /accounts/users/{id}/
    - DELETE /accounts/users/{id}/
    - POST   /accounts/users/{id}/toggle-active/
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminRole]

    # ---------- Filtres inline ----------
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['role', 'is_active', 'is_verified']
    search_fields = ['username', 'email', 'phone', 'first_name', 'last_name']
    ordering_fields = ['created_at', 'username', 'role']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        if not user.is_admin_role:
            return User.objects.none()
        return User.objects.all().order_by('-created_at')

    @action(detail=True, methods=['post'], url_path='toggle-active')
    def toggle_active(self, request, pk=None):
        user = self.get_object()

        if user == request.user:
            return Response(
                {"detail": "Vous ne pouvez pas désactiver votre propre compte."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        return Response({'id': user.id, 'is_active': user.is_active})