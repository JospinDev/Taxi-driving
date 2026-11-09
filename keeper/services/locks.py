"""
Verrous distribués Redis.

Le verrou Redis sert à échouer VITE (409) quand plusieurs chauffeurs tapent
« Accepter » en même temps, sans faire la queue sur la ligne PostgreSQL.
La vérité reste en base : SELECT … FOR UPDATE + contraintes uniques.
Si Redis est indisponible, on continue en s'appuyant uniquement sur la base
(toujours correct, juste un peu plus de contention).
"""
import logging
from contextlib import contextmanager

import redis
from django.conf import settings

from keeper.services.exceptions import ConflictError

logger = logging.getLogger(__name__)

_client = None


def get_redis():
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2,
        )
    return _client


@contextmanager
def redis_lock(name, timeout=10, message=None):
    """Verrou non bloquant : lève ConflictError s'il est déjà détenu."""
    lock = get_redis().lock(f"keeper:lock:{name}", timeout=timeout)
    try:
        acquired = lock.acquire(blocking=False)
    except redis.exceptions.RedisError:
        logger.warning("Redis indisponible, verrou '%s' ignoré (fallback DB).", name)
        yield
        return

    if not acquired:
        raise ConflictError(message or "Opération déjà en cours, réessayez.")
    try:
        yield
    finally:
        try:
            lock.release()
        except redis.exceptions.LockError:
            # TTL expiré pendant le traitement : la base a garanti la cohérence
            logger.warning("Verrou '%s' expiré avant libération.", name)
        except redis.exceptions.RedisError:
            logger.warning("Impossible de libérer le verrou '%s'.", name)
