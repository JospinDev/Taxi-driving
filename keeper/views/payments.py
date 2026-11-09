from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from keeper.models import Wallet, WalletTransaction
from keeper.permissions.accounts import IsAdminRole, IsDriver
from keeper.serializers.payments import (
    AdjustmentSerializer, PayoutSerializer, WalletSerializer, WalletTransactionSerializer,
)
from keeper.services import payments


class WalletViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    - GET  /payments/wallets/                 LIST (admin)
    - GET  /payments/wallets/{id}/            RETRIEVE (admin)
    - GET  /payments/wallets/me/              portefeuille du chauffeur connecté
    - POST /payments/wallets/{id}/payout/     versement au chauffeur (admin)
    - POST /payments/wallets/{id}/adjust/     ajustement signé (admin)
    """
    queryset = Wallet.objects.none()  # schéma OpenAPI ; le vrai filtrage est dans get_queryset
    serializer_class = WalletSerializer
    lookup_value_regex = r'[0-9]+'
    permission_classes = [IsAdminRole]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {'balance': ['gte', 'lte']}
    search_fields = ['driver__user__username', 'driver__user__phone']
    ordering_fields = ['balance', 'total_earned', 'updated_at']
    ordering = ['balance']

    def get_queryset(self):
        return Wallet.objects.select_related('driver__user')

    @action(detail=False, methods=['get'], url_path='me', permission_classes=[IsDriver])
    def me(self, request):
        profile = getattr(request.user, 'driver_profile', None)
        if profile is None:
            return Response({'detail': "Profil chauffeur introuvable."}, status=status.HTTP_404_NOT_FOUND)
        wallet, _ = Wallet.objects.select_related('driver__user').get_or_create(driver=profile)
        return Response(self.get_serializer(wallet).data)

    @extend_schema(request=PayoutSerializer, responses=WalletTransactionSerializer)
    @action(detail=True, methods=['post'], url_path='payout')
    def payout(self, request, pk=None):
        serializer = PayoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tx = payments.payout(pk, admin=request.user, **serializer.validated_data)
        return Response(WalletTransactionSerializer(tx).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=AdjustmentSerializer, responses=WalletTransactionSerializer)
    @action(detail=True, methods=['post'], url_path='adjust')
    def adjust(self, request, pk=None):
        serializer = AdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tx = payments.adjust(pk, admin=request.user, **serializer.validated_data)
        return Response(WalletTransactionSerializer(tx).data, status=status.HTTP_201_CREATED)


class WalletTransactionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    - GET /payments/transactions/       chauffeur : les siennes / admin : toutes
    - GET /payments/transactions/{id}/
    """
    queryset = WalletTransaction.objects.none()  # schéma OpenAPI ; le vrai filtrage est dans get_queryset
    serializer_class = WalletTransactionSerializer
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = {
        'wallet': ['exact'],
        'type': ['exact'],
        'ride': ['exact'],
        'created_at': ['gte', 'lte'],
    }
    ordering_fields = ['created_at', 'amount']
    ordering = ['-created_at']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset
        user = self.request.user
        qs = WalletTransaction.objects.select_related('created_by')
        if user.is_admin_role:
            return qs
        if user.is_driver:
            return qs.filter(wallet__driver__user=user)
        return WalletTransaction.objects.none()
