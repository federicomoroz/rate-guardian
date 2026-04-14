import logging

import redis

from app.core.config import REDIS_URL

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """
    Returns the shared Redis client, creating it on first call.
    S — single responsibility: manage one shared connection.
    D — callers depend on this function, not on redis.Redis directly.
    """
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
        logger.info("Redis client created — %s", REDIS_URL)
    return _client
