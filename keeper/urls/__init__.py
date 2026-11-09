from django.urls import include, path

urlpatterns = [
    path('accounts/', include('keeper.urls.accounts')),
    path('vehicles/', include('keeper.urls.vehicles')),
    path('contracts/', include('keeper.urls.contracts')),
    path('pricing/', include('keeper.urls.pricing')),
    path('rides/', include('keeper.urls.rides')),
    path('payments/', include('keeper.urls.payments')),
    path('notifications/', include('keeper.urls.notifications')),
]
