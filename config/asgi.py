import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# Initialise Django AVANT d'importer quoi que ce soit qui touche aux modèles
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from keeper.realtime.middleware import JWTAuthMiddleware  # noqa: E402
from keeper.realtime.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    # Pas de validation d'Origin : les apps mobiles n'en envoient pas, et
    # l'authentification se fait par JWT (pas de cookie → pas de CSRF WebSocket).
    'websocket': JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
})
