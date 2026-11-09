from rest_framework.routers import DefaultRouter

from keeper.views.payments import WalletTransactionViewSet, WalletViewSet

router = DefaultRouter()
router.register('wallets', WalletViewSet, basename='wallet')
router.register('transactions', WalletTransactionViewSet, basename='wallet-transaction')

urlpatterns = router.urls
