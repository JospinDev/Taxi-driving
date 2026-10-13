from django.urls import path, include

urlpatterns = [
    # On ajoutera les modules ici au fur et à mesure :
    path('accounts/', include('keeper.urls.accounts')),
]