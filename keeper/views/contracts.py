from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from keeper.models import Contract
from keeper.permissions.accounts import IsAdminRole, IsDriver
from keeper.permissions.contracts import IsContractOwnerOrAdmin
from keeper.serializers.common import EmptySerializer
from keeper.serializers.contracts import (
    ContractCreateSerializer, ContractSerializer, ContractUpdateSerializer,
)


class ContractViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    - GET    /contracts/                    LIST (chauffeur: les siens / admin: tous)
    - POST   /contracts/                    CREATE (admin)
    - GET    /contracts/{id}/               RETRIEVE (propriétaire ou admin)
    - PATCH  /contracts/{id}/               UPDATE (admin, si non signé)
    - POST   /contracts/{id}/sign/          SIGNER (chauffeur propriétaire)
    - POST   /contracts/{id}/activate/      ACTIVER (admin, après signature)
    - POST   /contracts/{id}/deactivate/    DÉSACTIVER (admin)
    """
    queryset = Contract.objects.select_related('driver__user', 'created_by').all()
    serializer_class = ContractSerializer
    permission_classes = [IsAuthenticated]

    # ---------- Filtres inline ----------
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        'is_active': ['exact'],
        'driver': ['exact'],
        'commission_percent': ['exact', 'gte', 'lte'],
        'signed_at': ['isnull'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = ['driver__user__username', 'driver__user__phone', 'content']
    ordering_fields = ['created_at', 'commission_percent', 'signed_at']
    ordering = ['-created_at']

    # ---------- Permissions dynamiques ----------
    def get_permissions(self):
        if self.action in ('create', 'activate', 'deactivate'):
            return [IsAdminRole()]
        if self.action in ('update', 'partial_update'):
            return [IsAdminRole()]
        if self.action == 'sign':
            return [IsDriver()]
        if self.action in ('retrieve',):
            return [IsAuthenticated(), IsContractOwnerOrAdmin()]
        return super().get_permissions()

    # ---------- Queryset dynamique ----------
    def get_queryset(self):
        user = self.request.user
        base = Contract.objects.select_related('driver__user', 'created_by').all()

        if user.is_superuser or user.is_admin_role:
            return base.order_by('-id')

        if user.is_driver:
            profile = getattr(user, 'driver_profile', None)
            if not profile:
                return Contract.objects.none()
            return base.filter(driver=profile).order_by('-id')

        return Contract.objects.none()

    # ---------- Serializer dynamique ----------
    def get_serializer_class(self):
        if self.action == 'create':
            return ContractCreateSerializer
        if self.action in ('update', 'partial_update'):
            return ContractUpdateSerializer
        return ContractSerializer

    # ---------- CREATE ----------
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contract = serializer.save(created_by=request.user)
        return Response(
            ContractSerializer(contract).data,
            status=status.HTTP_201_CREATED,
        )

    # ---------- SIGN (chauffeur) ----------
    @action(
        detail=True, methods=['post'], url_path='sign',
        serializer_class=EmptySerializer,
        permission_classes=[IsAuthenticated, IsDriver],
    )
    def sign(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        contract = self.get_object()   # get_queryset filtre déjà par driver

        if contract.is_signed:
            return Response(
                {'detail': "Contrat déjà signé."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Récupérer l'IP du client (derrière proxy éventuel)
        ip = (
            request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR')
        )
        contract.sign(ip_address=ip)

        return Response({
            'detail': "Contrat signé avec succès.",
            'contract': ContractSerializer(contract).data,
        })

    # ---------- ACTIVATE (admin) ----------
    @action(
        detail=True, methods=['post'], url_path='activate',
        serializer_class=EmptySerializer,
        permission_classes=[IsAdminRole],
    )
    def activate(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        contract = self.get_object()

        if not contract.is_signed:
            return Response(
                {'detail': "Impossible d'activer un contrat non signé."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Désactiver les autres contrats actifs du même chauffeur
        Contract.objects.filter(
            driver=contract.driver, is_active=True,
        ).exclude(pk=contract.pk).update(is_active=False)

        contract.is_active = True
        contract.save(update_fields=['is_active', 'updated_at'])
        return Response(ContractSerializer(contract).data)

    # ---------- DEACTIVATE (admin) ----------
    @action(
        detail=True, methods=['post'], url_path='deactivate',
        serializer_class=EmptySerializer,
        permission_classes=[IsAdminRole],
    )
    def deactivate(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        contract = self.get_object()
        contract.is_active = False
        contract.save(update_fields=['is_active', 'updated_at'])
        return Response(ContractSerializer(contract).data)