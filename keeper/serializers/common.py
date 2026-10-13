from rest_framework import serializers


class EmptySerializer(serializers.Serializer):
    """
    Serializer qui n'accepte AUCUN champ dans le body.
    Utilisable pour les actions 'trigger' (activate, deactivate, approve…).
    """

    def to_internal_value(self, data):
        if data:
            raise serializers.ValidationError(
                "Aucun champ n'est attendu dans le body de cette requête."
            )
        return {}