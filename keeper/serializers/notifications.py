from rest_framework import serializers

from keeper.models import Notification
from keeper.services.notifications import DEFAULT_POPUP, POPUP


class NotificationSerializer(serializers.ModelSerializer):
    popup = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id', 'channel', 'event', 'title', 'body', 'data', 'ride',
            'status', 'attempts', 'error', 'is_read', 'read_at', 'popup',
            'created_at', 'sent_at',
        ]
        read_only_fields = fields

    def get_popup(self, obj) -> dict:
        return POPUP.get(obj.event, DEFAULT_POPUP)

    def to_representation(self, obj):
        data = super().to_representation(obj)
        request = self.context.get('request')
        if request and not request.user.is_admin_role:
            # Détails techniques réservés à l'admin
            for key in ('error', 'attempts', 'status', 'sent_at'):
                data.pop(key, None)
        return data
