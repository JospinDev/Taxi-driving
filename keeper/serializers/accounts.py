from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from keeper.models import User, DriverProfile


# ============================================================
# LECTURE
# ============================================================
class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'role', 'phone', 'whatsapp', 'is_verified',
            'is_active', 'created_at',
        ]
        read_only_fields = ['id', 'role', 'is_verified', 'is_active', 'created_at']


class DriverProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = DriverProfile
        fields = [
            'id', 'user', 'status', 'license_number',
            'last_location', 'last_location_at',
            'rating', 'total_rides', 'is_approved', 'approved_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'last_location', 'last_location_at',
            'rating', 'total_rides', 'is_approved', 'approved_at',
            'created_at', 'updated_at',
        ]


# ============================================================
# INSCRIPTION CLIENT
# ============================================================
class ClientRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = [
            'username', 'email', 'phone', 'whatsapp',
            'first_name', 'last_name',
            'password', 'password_confirm',
        ]

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('password_confirm'):
            raise serializers.ValidationError({'password_confirm': "Les mots de passe ne correspondent pas."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data, role=User.Role.CLIENT)
        user.set_password(password)
        user.save()
        return user


# ============================================================
# INSCRIPTION CHAUFFEUR
# ============================================================
class DriverRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    password_confirm = serializers.CharField(write_only=True)
    license_number = serializers.CharField(max_length=50)

    class Meta:
        model = User
        fields = [
            'username', 'email', 'phone', 'whatsapp',
            'first_name', 'last_name',
            'password', 'password_confirm',
            'license_number',
        ]

    def validate_phone(self, value):
        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("Ce numéro est déjà utilisé.")
        return value

    def validate_license_number(self, value):
        if DriverProfile.objects.filter(license_number=value).exists():
            raise serializers.ValidationError("Ce numéro de permis est déjà enregistré.")
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('password_confirm'):
            raise serializers.ValidationError({'password_confirm': "Les mots de passe ne correspondent pas."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop('password')
        license_number = validated_data.pop('license_number')

        user = User(**validated_data, role=User.Role.DRIVER)
        user.set_password(password)
        user.save()

        DriverProfile.objects.create(user=user, license_number=license_number)
        return user


# ============================================================
# LOGIN (JWT custom avec infos user)
# ============================================================
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            username=attrs.get('username'),
            password=attrs.get('password'),
        )
        if not user:
            raise serializers.ValidationError("Identifiants invalides.")
        if not user.is_active:
            raise serializers.ValidationError("Compte désactivé.")

        refresh = RefreshToken.for_user(user)
        data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': UserSerializer(user).data,
        }
        # Si chauffeur → inclure le profil
        if user.is_driver:
            profile = getattr(user, 'driver_profile', None)
            if profile:
                data['driver_profile'] = DriverProfileSerializer(profile).data
        return data


# ============================================================
# MISE À JOUR DU PROFIL
# ============================================================
class UpdateMeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['email', 'whatsapp', 'first_name', 'last_name', 'fcm_token']


# ============================================================
# MISE À JOUR POSITION CHAUFFEUR
# ============================================================
class DriverLocationSerializer(serializers.Serializer):
    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)


# ============================================================
# CHANGEMENT DE STATUT
# ============================================================
class DriverStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[DriverProfile.Status.AVAILABLE, DriverProfile.Status.OFFLINE]
    )