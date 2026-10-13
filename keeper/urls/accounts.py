from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from keeper.views.accounts import (
    AuthViewSet, MeViewSet, DriverProfileViewSet, UserViewSet,
)

router = DefaultRouter()
router.register('auth', AuthViewSet, basename='auth')
router.register('drivers', DriverProfileViewSet, basename='driver')
router.register('users', UserViewSet, basename='user')

urlpatterns = [
    path(
        'me/',
        MeViewSet.as_view({'get': 'retrieve', 'patch': 'partial_update'}),
        name='me',
    ),
    path('token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('', include(router.urls)),
]