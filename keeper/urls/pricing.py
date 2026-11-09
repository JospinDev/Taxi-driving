from rest_framework.routers import DefaultRouter
from keeper.views.pricing import PricingRuleViewSet

router = DefaultRouter()
router.register('rules', PricingRuleViewSet, basename='pricing-rule')

urlpatterns = router.urls