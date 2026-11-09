from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from keeper.models import Notification
from keeper.serializers.common import EmptySerializer
from keeper.serializers.notifications import NotificationSerializer
from keeper.services.notifications import unread_count

UnreadCountSerializer = inline_serializer('UnreadCount', {'unread_count': serializers.IntegerField()})


class NotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Boîte de réception IN_APP (client & chauffeur) :
    - GET  /notifications/                 mes notifications in-app (admin : tous canaux)
    - GET  /notifications/{id}/
    - GET  /notifications/unread-count/    badge
    - POST /notifications/{id}/read/       marquer comme lue
    - POST /notifications/read-all/        tout marquer comme lu

    Temps réel : ws/notifications/?token=<JWT>
      {"event": "notification", "data": {..., "popup": {"ttl_s", "priority"}, "unread_count"}}
      {"event": "notification.unread_count", "data": {"unread_count": n}}
    """
    queryset = Notification.objects.none()  # schéma OpenAPI ; le vrai filtrage est dans get_queryset
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    lookup_value_regex = r'[0-9]+'

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ['channel', 'event', 'status', 'is_read', 'ride', 'user']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset
        user = self.request.user
        if user.is_admin_role and self.action in ('list', 'retrieve'):
            return Notification.objects.select_related('user')  # supervision tous canaux
        # Utilisateur : seulement sa boîte in-app (push/WhatsApp = doublons techniques)
        return Notification.objects.filter(user=user, channel=Notification.Channel.IN_APP)

    @extend_schema(responses=UnreadCountSerializer)
    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        return Response({'unread_count': unread_count(request.user.pk)})

    @extend_schema(request=EmptySerializer, responses=NotificationSerializer)
    @action(detail=True, methods=['post'], url_path='read')
    def read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            # UPDATE conditionnel : idempotent même avec plusieurs appareils
            Notification.objects.filter(pk=notification.pk, is_read=False).update(
                is_read=True, read_at=notification.read_at,
            )
        data = self.get_serializer(notification).data
        data['unread_count'] = unread_count(request.user.pk)
        return Response(data)

    @extend_schema(request=EmptySerializer)
    @action(detail=False, methods=['post'], url_path='read-all')
    def read_all(self, request):
        count = Notification.objects.filter(
            user=request.user, channel=Notification.Channel.IN_APP, is_read=False,
        ).update(is_read=True, read_at=timezone.now())
        return Response({'updated': count, 'unread_count': 0})
