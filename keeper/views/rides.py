from django.conf import settings
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from keeper.models import Ride, RideOffer
from keeper.permissions.accounts import IsAdminRole, IsClient, IsDriverApproved
from keeper.serializers.chat import RideMessageCreateSerializer, RideMessageSerializer
from keeper.serializers.common import EmptySerializer
from keeper.serializers.rides import (
    ConfirmPaymentSerializer, NearbyDriverSerializer, NearbyDriversQuerySerializer,
    RideCancelSerializer, RideCreateSerializer,
    RideEstimateResultSerializer, RideEstimateSerializer, RideOfferSerializer,
    RideRateSerializer, RideSerializer, RideTrackPointSerializer, TrackBatchSerializer,
)
from keeper.services import chat, dispatch, payments, tracking
from keeper.services.geo import make_point

RIDE_RELATED = ('client', 'driver__user', 'vehicle')


class RideViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Client :
      GET  /rides/nearby-drivers/?lat=&lng=  taxis disponibles autour (anonymisé)
      POST /rides/estimate/              estimation du prix
      POST /rides/                       commander (lance le dispatch)
      POST /rides/{id}/rate/             noter le chauffeur
    Chauffeur :
      GET  /rides/offers/                offres en attente
      POST /rides/{id}/accept/           accepter (verrou Redis + DB)
      POST /rides/{id}/reject/           refuser
      POST /rides/{id}/arrived/          arrivé au point de prise en charge
      POST /rides/{id}/start/            démarrer le compteur
      POST /rides/{id}/track/            lot de points GPS
      POST /rides/{id}/complete/         terminer → facture + portefeuille
    Commun :
      GET  /rides/  /rides/{id}/  /rides/current/  /rides/{id}/route/
      POST /rides/{id}/cancel/
      GET  /rides/{id}/messages/        fil de discussion
      POST /rides/{id}/messages/        envoyer un message
      POST /rides/{id}/messages/read/   marquer le fil comme lu
    Admin :
      POST /rides/{id}/confirm-payment/  paiement mobile money reçu
    """
    queryset = Ride.objects.none()  # schéma OpenAPI ; le vrai filtrage est dans get_queryset
    serializer_class = RideSerializer
    lookup_value_regex = r'[0-9]+'
    permission_classes = [IsAuthenticated]

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = {
        'status': ['exact', 'in'],
        'payment_method': ['exact'],
        'payment_status': ['exact'],
        'driver': ['exact'],
        'client': ['exact'],
        'requested_at': ['gte', 'lte'],
    }
    ordering_fields = ['requested_at', 'completed_at', 'fare_total']
    ordering = ['-requested_at']

    CLIENT_ACTIONS = ('estimate', 'create', 'rate', 'nearby_drivers')
    DRIVER_ACTIONS = ('offers', 'accept', 'reject', 'arrived', 'start', 'track', 'complete')

    def get_permissions(self):
        if self.action in self.CLIENT_ACTIONS:
            return [IsClient()]
        if self.action in self.DRIVER_ACTIONS:
            return [IsDriverApproved()]
        if self.action == 'confirm_payment':
            return [IsAdminRole()]
        return super().get_permissions()

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return self.queryset
        user = self.request.user
        qs = Ride.objects.select_related(*RIDE_RELATED)
        if user.is_admin_role:
            return qs
        if user.is_driver:
            profile = getattr(user, 'driver_profile', None)
            return qs.filter(driver=profile) if profile else Ride.objects.none()
        return qs.filter(client=user)

    def _driver(self):
        return self.request.user.driver_profile

    def _ride_response(self, ride, http_status=status.HTTP_200_OK):
        ride = Ride.objects.select_related(*RIDE_RELATED).get(pk=ride.pk)
        return Response(self.get_serializer(ride).data, status=http_status)

    def _validated(self, serializer_class):
        serializer = serializer_class(data=self.request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    # ---------- CLIENT ----------
    @extend_schema(request=RideEstimateSerializer, responses=RideEstimateResultSerializer)
    @action(detail=False, methods=['post'], url_path='estimate')
    def estimate(self, request):
        data = self._validated(RideEstimateSerializer)
        est = dispatch.estimate(data['pickup'], data['dropoff'])
        return Response({
            'distance_km': est['distance_km'],
            'fare': est['fare'],
            'currency': settings.CURRENCY,
            'pricing_rule': est['pricing_rule'].name,
        })

    @extend_schema(request=RideCreateSerializer, responses=RideSerializer)
    def create(self, request):
        data = self._validated(RideCreateSerializer)
        ride = dispatch.create_ride(request.user, **data)
        return self._ride_response(ride, status.HTTP_201_CREATED)

    @extend_schema(request=RideRateSerializer, responses=RideSerializer)
    @action(detail=True, methods=['post'], url_path='rate')
    def rate(self, request, pk=None):
        data = self._validated(RideRateSerializer)
        ride = payments.rate_ride(pk, request.user, data['rating'], data['comment'])
        return self._ride_response(ride)

    @extend_schema(parameters=[NearbyDriversQuerySerializer], responses=NearbyDriverSerializer(many=True))
    @action(detail=False, methods=['get'], url_path='nearby-drivers')
    def nearby_drivers(self, request):
        query = NearbyDriversQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        point = make_point(query.validated_data['lng'], query.validated_data['lat'])
        drivers = dispatch.nearby_available_drivers(point, query.validated_data['radius_m'])
        return Response(drivers)

    # ---------- COMMUN ----------
    @action(detail=False, methods=['get'], url_path='current')
    def current(self, request):
        ride = self.get_queryset().filter(status__in=Ride.ACTIVE_STATUSES).first()
        if ride is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(self.get_serializer(ride).data)

    @extend_schema(request=RideCancelSerializer, responses=RideSerializer)
    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        data = self._validated(RideCancelSerializer)
        ride = dispatch.cancel_ride(pk, request.user, data['reason'])
        if ride.client_id != request.user.pk and not request.user.is_admin_role:
            # Le chauffeur s'est désisté : la course ne lui appartient plus
            return Response({'detail': "Course relâchée, elle est proposée à d'autres chauffeurs."})
        return self._ride_response(ride)

    @extend_schema(responses=RideTrackPointSerializer(many=True))
    @action(detail=True, methods=['get'], url_path='route')
    def route(self, request, pk=None):
        ride = self.get_object()
        points = ride.track_points.only(
            'location', 'speed_kmh', 'heading', 'is_traffic', 'recorded_at',
        )
        page = self.paginate_queryset(points)
        serializer = RideTrackPointSerializer(page if page is not None else points, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    # ---------- CHAT (client ↔ chauffeur) ----------
    @extend_schema(methods=['GET'], responses=RideMessageSerializer(many=True))
    @extend_schema(
        methods=['POST'], request=RideMessageCreateSerializer,
        responses=RideMessageSerializer,
    )
    @action(detail=True, methods=['get', 'post'], url_path='messages')
    def messages(self, request, pk=None):
        if request.method == 'POST':
            data = self._validated(RideMessageCreateSerializer)
            message = chat.send_message(pk, request.user, data['body'])
            return Response(
                RideMessageSerializer(message, context=self.get_serializer_context()).data,
                status=status.HTTP_201_CREATED,
            )

        ride = chat.get_ride_for_chat(pk, request.user)
        thread = chat.thread(ride)
        page = self.paginate_queryset(thread)
        serializer = RideMessageSerializer(
            page if page is not None else thread, many=True,
            context=self.get_serializer_context(),
        )
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @extend_schema(request=EmptySerializer, responses=None)
    @action(detail=True, methods=['post'], url_path='messages/read')
    def read_messages(self, request, pk=None):
        return Response({'updated': chat.mark_thread_read(pk, request.user)})

    # ---------- CHAUFFEUR ----------
    @extend_schema(responses=RideOfferSerializer(many=True))
    @action(detail=False, methods=['get'], url_path='offers')
    def offers(self, request):
        qs = (
            RideOffer.objects
            .filter(driver=self._driver(), status=RideOffer.Status.PENDING,
                    ride__status=Ride.Status.SEARCHING)
            .select_related('ride')
            .order_by('-created_at')
        )
        return Response(RideOfferSerializer(qs, many=True).data)

    @extend_schema(request=EmptySerializer, responses=RideSerializer)
    @action(detail=True, methods=['post'], url_path='accept')
    def accept(self, request, pk=None):
        ride = dispatch.accept_ride(pk, self._driver())
        return self._ride_response(ride)

    @extend_schema(request=EmptySerializer, responses=None)
    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        dispatch.reject_offer(pk, self._driver())
        return Response({'detail': "Offre refusée."})

    @extend_schema(request=EmptySerializer, responses=RideSerializer)
    @action(detail=True, methods=['post'], url_path='arrived')
    def arrived(self, request, pk=None):
        ride = tracking.mark_arrived(pk, self._driver())
        return self._ride_response(ride)

    @extend_schema(request=EmptySerializer, responses=RideSerializer)
    @action(detail=True, methods=['post'], url_path='start')
    def start(self, request, pk=None):
        ride = tracking.start_ride(pk, self._driver())
        return self._ride_response(ride)

    @extend_schema(request=TrackBatchSerializer, responses=None)
    @action(detail=True, methods=['post'], url_path='track')
    def track(self, request, pk=None):
        data = self._validated(TrackBatchSerializer)
        result = tracking.record_track_points(pk, self._driver(), data['points'])
        return Response(result)

    @extend_schema(request=EmptySerializer, responses=RideSerializer)
    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        ride = payments.complete_ride(pk, self._driver())
        return self._ride_response(ride)

    # ---------- ADMIN ----------
    @extend_schema(request=ConfirmPaymentSerializer, responses=RideSerializer)
    @action(detail=True, methods=['post'], url_path='confirm-payment')
    def confirm_payment(self, request, pk=None):
        data = self._validated(ConfirmPaymentSerializer)
        ride = payments.confirm_payment(pk, request.user, data['reference'])
        return self._ride_response(ride)
