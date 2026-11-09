"""Authentification WebSocket par JWT : ws://…/ws/…/?token=<access>"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _user_from_token(raw_token):
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    auth = JWTAuthentication()
    try:
        return auth.get_user(auth.get_validated_token(raw_token))
    except (InvalidToken, TokenError, AuthenticationFailed):
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        params = parse_qs(scope.get('query_string', b'').decode())
        token = (params.get('token') or [None])[0]
        scope['user'] = await _user_from_token(token) if token else AnonymousUser()
        return await super().__call__(scope, receive, send)
