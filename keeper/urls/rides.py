from rest_framework.routers import DefaultRouter

from keeper.views.rides import RideViewSet

router = DefaultRouter()
router.register('', RideViewSet, basename='ride')

urlpatterns = router.urls
