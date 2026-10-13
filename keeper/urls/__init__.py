from django.urls import include, path

urlpatterns = [
    path('accounts/', include('keeper.urls.accounts')),
    path('vehicles/', include('keeper.urls.vehicles')),
]