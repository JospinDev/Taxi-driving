from rest_framework import serializers

from keeper.models import RideMessage


class RideMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()
    sender_role = serializers.CharField(source='sender.role', read_only=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = RideMessage
        fields = [
            'id', 'ride', 'sender', 'sender_name', 'sender_role',
            'body', 'is_mine', 'read_at', 'created_at',
        ]
        read_only_fields = fields

    def get_sender_name(self, obj):
        return obj.sender.get_full_name() or obj.sender.username

    def get_is_mine(self, obj):
        user = self.context.get('request').user if self.context.get('request') else None
        return user is not None and obj.sender_id == user.pk


class RideMessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=1000, trim_whitespace=True)

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le message ne peut pas être vide.")
        return value.strip()
