import time
import logging

import redis

from app.models.services.interfaces import RateLimiterBase, RateLimitResult

logger = logging.getLogger(__name__)

_PREFIX = "rg:rl:"


class SlidingWindowRateLimiter(RateLimiterBase):
    """
    Sliding-window rate limiter backed by a Redis Sorted Set.

    Each key maps to a ZSET of request timestamps (epoch floats).
    On every check:
      1. Remove timestamps outside the current window.
      2. Count the remaining ones.
      3. If count < limit: add the current timestamp and allow.
      4. If count >= limit: reject without consuming quota.

    S — responsible only for the sliding-window algorithm.
    L — fully substitutable for RateLimiterBase; never calls sys.exit
        or modifies state on rejection.
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        redis_key = f"{_PREFIX}{key}"
        now       = time.time()
        window_start = now - window_seconds

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, "-inf", window_start)
        pipe.zcard(redis_key)
        pipe.execute()

        current_count = self._redis.zcard(redis_key)

        if current_count < limit:
            self._redis.zadd(redis_key, {str(now): now})
            self._redis.expire(redis_key, window_seconds + 1)
            remaining = limit - current_count - 1
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=remaining,
                reset_in=window_seconds,
            )

        # Rejected — find when the oldest entry expires
        oldest = self._redis.zrange(redis_key, 0, 0, withscores=True)
        reset_in = int(window_seconds - (now - oldest[0][1])) if oldest else window_seconds

        return RateLimitResult(
            allowed=False,
            limit=limit,
            remaining=0,
            reset_in=max(reset_in, 0),
        )
