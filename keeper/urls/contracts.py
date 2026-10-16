from rest_framework.routers import DefaultRouter
from keeper.views.contracts import ContractViewSet

router = DefaultRouter()
router.register('', ContractViewSet, basename='contract')

urlpatterns = router.urls