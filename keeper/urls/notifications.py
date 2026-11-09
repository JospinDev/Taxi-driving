from rest_framework.routers import DefaultRouter

from keeper.views.notifications import NotificationViewSet

router = DefaultRouter()
router.register('', NotificationViewSet, basename='notification')

urlpatterns = router.urls
