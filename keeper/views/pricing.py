from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from keeper.models import PricingRule
from keeper.permissions.accounts import IsAdminRole
from keeper.serializers.common import EmptySerializer
from keeper.serializers.pricing import PricingRuleSerializer, PricingRuleWriteSerializer


class PricingRuleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    - GET    /pricing/rules/                    LIST (auth)
    - POST   /pricing/rules/                    CREATE (admin)
    - GET    /pricing/rules/{id}/               RETRIEVE (auth)
    - PATCH  /pricing/rules/{id}/               UPDATE (admin, si non active)
    - DELETE /pricing/rules/{id}/               DESTROY (admin, si non active)
    - GET    /pricing/rules/active/             RETRIEVE la grille active
    - POST   /pricing/rules/{id}/activate/      ACTIVER (admin)
    - POST   /pricing/rules/{id}/deactivate/    DÉSACTIVER (admin)
    """
    queryset = PricingRule.objects.select_related('created_by').all()
    serializer_class = PricingRuleSerializer
    permission_classes = [IsAuthenticated]

    # ---------- Filtres inline ----------
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        'is_active': ['exact'],
        'effective_from': ['gte', 'lte'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = ['name']
    ordering_fields = ['effective_from', 'price_per_km', 'created_at']
    ordering = ['-effective_from']

    # ---------- Permissions dynamiques ----------
    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy',
                           'activate', 'deactivate'):
            return [IsAdminRole()]
        return super().get_permissions()

    # ---------- Queryset ----------
    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role:
            return PricingRule.objects.select_related('created_by').all().order_by('-effective_from')
        # Non-admin : seulement les grilles actives
        return PricingRule.objects.filter(is_active=True).order_by('-effective_from')

    # ---------- Serializer dynamique ----------
    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return PricingRuleWriteSerializer
        if self.action in ('activate', 'deactivate'):
            return EmptySerializer
        return PricingRuleSerializer

    # ---------- CREATE ----------
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = serializer.save(created_by=request.user, is_active=False)
        return Response(
            PricingRuleSerializer(rule).data,
            status=status.HTTP_201_CREATED,
        )

    # ---------- UPDATE (bloqué si active ou déjà utilisée) ----------
    @transaction.atomic
    def update(self, request, *args, **kwargs):
        rule = self.get_object()
        if rule.is_active:
            return Response(
                {'detail': "Impossible de modifier une grille active. Désactivez-la d'abord."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Les courses gardent un lien vers leur grille : la modifier fausserait
        # la facturation des courses en cours et l'historique.
        if rule.rides.exists():
            return Response(
                {'detail': "Grille déjà utilisée par des courses. Créez-en une nouvelle."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().update(request, *args, **kwargs)

    # ---------- DESTROY (bloqué si active ou déjà utilisée) ----------
    def destroy(self, request, *args, **kwargs):
        rule = self.get_object()
        if rule.is_active:
            return Response(
                {'detail': "Impossible de supprimer une grille active."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if rule.rides.exists():
            return Response(
                {'detail': "Grille utilisée par des courses : suppression impossible."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    # ---------- GET ACTIVE ----------
    @action(
        detail=False, methods=['get'], url_path='active',
        permission_classes=[IsAuthenticated],
    )
    def active(self, request):
        rule = PricingRule.objects.filter(is_active=True).first()
        if not rule:
            return Response(
                {'detail': "Aucune grille tarifaire active."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(PricingRuleSerializer(rule).data)

    # ---------- ACTIVATE ----------
    @action(
        detail=True, methods=['post'], url_path='activate',
        serializer_class=EmptySerializer,
        permission_classes=[IsAdminRole],
    )
    def activate(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        rule = self.get_object()

        with transaction.atomic():
            # Verrouille toutes les grilles actives : deux activations simultanées
            # sont sérialisées au lieu de violer la contrainte unique (500).
            list(PricingRule.objects.select_for_update().filter(is_active=True))
            PricingRule.objects.filter(is_active=True).exclude(pk=rule.pk).update(is_active=False)
            rule.is_active = True
            rule.save(update_fields=['is_active', 'updated_at'])
        return Response(PricingRuleSerializer(rule).data)

    # ---------- DEACTIVATE ----------
    @action(
        detail=True, methods=['post'], url_path='deactivate',
        serializer_class=EmptySerializer,
        permission_classes=[IsAdminRole],
    )
    def deactivate(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        rule = self.get_object()
        rule.is_active = False
        rule.save(update_fields=['is_active', 'updated_at'])
        return Response(PricingRuleSerializer(rule).data)