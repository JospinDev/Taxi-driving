import os
import uuid

from django.core.files.storage import default_storage
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework.parsers import FormParser, MultiPartParser
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from keeper.models import DocumentType, Vehicle
from keeper.permissions.accounts import IsAdminRole, IsDriverApproved
from keeper.permissions.vehicles import IsVehicleOwnerOrAdmin
from keeper.serializers.common import EmptySerializer
from keeper.serializers.vehicles import (
    DocumentTypeSerializer, DocumentUploadSerializer,
    DocumentValidateSerializer, VehicleCreateSerializer,
    VehicleSerializer, VehicleUpdateSerializer,
)


# ============================================================
# VEHICLE VIEWSET
# ============================================================
class VehicleViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    - GET    /vehicles/                          LIST
    - POST   /vehicles/                          CREATE (chauffeur approuvé)
    - GET    /vehicles/{id}/                     RETRIEVE
    - PATCH  /vehicles/{id}/                     UPDATE (propriétaire ou admin)
    - DELETE /vehicles/{id}/                     DESTROY (propriétaire ou admin)
    - POST   /vehicles/{id}/upload-document/     uploader/remplacer un doc
    - DELETE /vehicles/{id}/documents/{type}/    supprimer un doc non validé
    - POST   /vehicles/{id}/validate-document/   ADMIN
    - POST   /vehicles/{id}/activate/            ADMIN
    - POST   /vehicles/{id}/deactivate/          ADMIN
    """
    queryset = Vehicle.objects.select_related('driver__user').all()
    serializer_class = VehicleSerializer
    permission_classes = [IsAuthenticated]

    # ---------- Filtres inline ----------
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        'is_active': ['exact'],
        'brand': ['exact', 'icontains'],
        'year': ['exact', 'gte', 'lte'],
        'seats': ['exact', 'gte', 'lte'],
        'driver': ['exact'],
        'created_at': ['gte', 'lte'],
    }
    search_fields = ['plate_number', 'brand', 'model', 'color',
                     'driver__user__username']
    ordering_fields = ['created_at', 'year', 'brand']
    ordering = ['-created_at']

    # ---------- Permissions dynamiques ----------
    def get_permissions(self):
        if self.action in ('validate_document', 'activate', 'deactivate'):
            return [IsAdminRole()]
        if self.action in ('create',):
            return [IsDriverApproved()]
        if self.action in ('update', 'partial_update', 'destroy',
                           'upload_document', 'delete_document'):
            return [IsAuthenticated(), IsVehicleOwnerOrAdmin()]
        return super().get_permissions()

    # ---------- Queryset dynamique ----------
    def get_queryset(self):
        user = self.request.user
        base = Vehicle.objects.select_related('driver__user').all()

        if user.is_superuser or user.is_admin_role:
            return base.order_by('-id')

        if user.is_driver:
            profile = getattr(user, 'driver_profile', None)
            if not profile:
                return Vehicle.objects.none()
            return base.filter(driver=profile).order_by('-id')

        return Vehicle.objects.none()

    # ---------- Serializer dynamique ----------
    def get_serializer_class(self):
        if self.action == 'create':
            return VehicleCreateSerializer
        if self.action in ('update', 'partial_update'):
            return VehicleUpdateSerializer
        if self.action == 'upload_document':
            return DocumentUploadSerializer
        if self.action == 'validate_document':
            return DocumentValidateSerializer
        return VehicleSerializer

    # ---------- CREATE ----------
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = getattr(request.user, 'driver_profile', None)
        if not profile:
            return Response(
                {'detail': "Profil chauffeur introuvable."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vehicle = serializer.save(driver=profile, documents={})
        return Response(
            VehicleSerializer(vehicle).data,
            status=status.HTTP_201_CREATED,
        )

    # ---------- UPLOAD DOCUMENT ----------
    @extend_schema(
        summary="Uploader un document pour un véhicule",
        description="Envoie un fichier (PDF ou image) pour un type de document donné.",
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'doc_type': {
                        'type': 'string',
                        'description': "Code du type de document (ex: carte_rose).",
                    },
                    'file': {
                        'type': 'string',
                        'format': 'binary',      # ⬅️ LA ligne magique
                        'description': "Fichier PDF ou image (max 5 Mo).",
                    },
                    'expires_at': {
                        'type': 'string',
                        'format': 'date',
                        'nullable': True,
                        'description': "Date d'expiration (obligatoire si le type l'exige).",
                    },
                },
                'required': ['doc_type', 'file'],
            }
        },
        responses={200: DocumentUploadSerializer},
    )
    @action(
        detail=True, methods=['post'], url_path='upload-document',
        serializer_class=DocumentUploadSerializer,
        permission_classes=[IsAuthenticated, IsVehicleOwnerOrAdmin],
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_document(self, request, pk=None):
        vehicle = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        doc_type = serializer.validated_data['doc_type']
        file = serializer.validated_data['file']
        expires_at = serializer.validated_data.get('expires_at')

        ext = os.path.splitext(file.name)[1].lower()
        filename = f"{doc_type}_{uuid.uuid4().hex}{ext}"
        rel_path = f"documents/vehicles/{vehicle.id}/{filename}"

        saved_path = default_storage.save(rel_path, file)

        vehicle.set_document(doc_type, {
            'file': saved_path,
            'original_name': file.name,
            'size_bytes': file.size,
            'uploaded_at': timezone.now().isoformat(),
            'expires_at': expires_at.isoformat() if expires_at else None,
            'is_validated': False,
            'validated_by': None,
            'validated_at': None,
            'note': None,
        })
        vehicle.save(update_fields=['documents', 'updated_at'])

        return Response({
            'detail': f"Document '{doc_type}' enregistré.",
            'document': vehicle.get_document(doc_type),
        })

    # ---------- DELETE DOCUMENT ----------
    @action(
        detail=True, methods=['delete'],
        url_path=r'documents/(?P<doc_type>[a-z_]+)',
        permission_classes=[IsAuthenticated, IsVehicleOwnerOrAdmin],
    )
    def delete_document(self, request, pk=None, doc_type=None):
        vehicle = self.get_object()

        if not DocumentType.objects.filter(code=doc_type).exists():
            return Response(
                {'detail': f"Type de document inconnu : {doc_type}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        doc = vehicle.get_document(doc_type)
        if not doc:
            return Response(
                {'detail': "Document inexistant."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if doc.get('is_validated'):
            return Response(
                {'detail': "Impossible de supprimer un document validé."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vehicle.remove_document(doc_type)
        vehicle.save(update_fields=['documents', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ---------- VALIDATE DOCUMENT (ADMIN) ----------
    @action(
        detail=True, methods=['post'], url_path='validate-document',
        serializer_class=DocumentValidateSerializer,
        permission_classes=[IsAdminRole],
    )
    def validate_document(self, request, pk=None):
        vehicle = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        doc_type = serializer.validated_data['doc_type']
        is_validated = serializer.validated_data['is_validated']
        note = serializer.validated_data.get('note', '')

        doc = vehicle.get_document(doc_type)
        if not doc:
            return Response(
                {'detail': f"Aucun document '{doc_type}' à valider."},
                status=status.HTTP_404_NOT_FOUND,
            )

        doc['is_validated'] = is_validated
        doc['validated_by'] = request.user.id
        doc['validated_at'] = timezone.now().isoformat()
        doc['note'] = note
        vehicle.set_document(doc_type, doc)
        vehicle.save(update_fields=['documents', 'updated_at'])

        return Response({
            'detail': f"Document '{doc_type}' {'validé' if is_validated else 'refusé'}.",
            'document': doc,
        })

    # ---------- ACTIVATE / DEACTIVATE (ADMIN, body strict) ----------
    @action(
        detail=True, methods=['post'], url_path='activate',
        serializer_class=EmptySerializer,
        permission_classes=[IsAdminRole],
    )
    def activate(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vehicle = self.get_object()
        if not vehicle.required_docs_validated:
            return Response(
                {'detail': "Tous les documents obligatoires doivent être validés avant activation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vehicle.is_active = True
        vehicle.save(update_fields=['is_active', 'updated_at'])
        return Response(VehicleSerializer(vehicle).data)

    @action(
        detail=True, methods=['post'], url_path='deactivate',
        serializer_class=EmptySerializer,
        permission_classes=[IsAdminRole],
    )
    def deactivate(self, request, pk=None):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vehicle = self.get_object()
        vehicle.is_active = False
        vehicle.save(update_fields=['is_active', 'updated_at'])
        return Response(VehicleSerializer(vehicle).data)


# ============================================================
# DOCUMENT TYPE VIEWSET (admin)
# ============================================================
class DocumentTypeViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    - GET    /vehicles/document-types/          LIST (auth)
    - POST   /vehicles/document-types/          CREATE (admin)
    - GET    /vehicles/document-types/{id}/     RETRIEVE (auth)
    - PATCH  /vehicles/document-types/{id}/     UPDATE (admin)
    - DELETE /vehicles/document-types/{id}/     DESTROY (admin)
    """
    queryset = DocumentType.objects.all()
    serializer_class = DocumentTypeSerializer
    permission_classes = [IsAuthenticated]

    # ---------- Filtres inline ----------
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['is_active', 'is_required', 'requires_expiry']
    search_fields = ['code', 'label', 'description']
    ordering_fields = ['order', 'code', 'created_at']
    ordering = ['order', 'code']

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAdminRole()]
        return super().get_permissions()

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role:
            return DocumentType.objects.all().order_by('order', 'code')
        return DocumentType.objects.filter(is_active=True).order_by('order', 'code')