from drf_spectacular.openapi import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


@extend_schema_field(OpenApiTypes.BINARY)
class BinaryFileField(serializers.FileField):
    """
    FileField qui force format='binary' dans le schéma OpenAPI.

    Sans ça, drf-spectacular génère 'string/uri' pour un FileField,
    et Swagger UI affiche un input texte au lieu d'un bouton 'Choose File'.

    Utilisation :
        file = BinaryFileField(max_length=255)
    """
    pass