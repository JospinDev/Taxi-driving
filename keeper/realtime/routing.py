from django.urls import path

from keeper.realtime.consumers import NotificationConsumer, RideConsumer

websocket_urlpatterns = [
    path('ws/notifications/', NotificationConsumer.as_asgi()),
    path('ws/rides/<int:ride_id>/', RideConsumer.as_asgi()),
]
