from rest_framework.routers import DefaultRouter
from keeper.views.vehicles import DocumentTypeViewSet, VehicleViewSet

router = DefaultRouter()
router.register('document-types', DocumentTypeViewSet, basename='document-type')
router.register('', VehicleViewSet, basename='vehicle')

urlpatterns = router.urls