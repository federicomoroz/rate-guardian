"""
Unit tests — SlidingWindowRateLimiter.
Covers: allow under limit, reject at limit, quota not consumed on reject,
        window expiry, per-key isolation, limit=1, large limits, reset_in.
"""

import time
import pytest
import fakeredis

from app.models.services.rate_limiter import SlidingWindowRateLimiter
from app.models.services.interfaces import RateLimitResult


@pytest.fixture()
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def rl(redis):
    return SlidingWindowRateLimiter(redis)


# ── Allow / reject basics ─────────────────────────────────────────────────────

def test_first_request_always_allowed(rl):
    r = rl.check("ip:1.2.3.4", limit=5, window_seconds=60)
    assert r.allowed is True
    assert r.limit == 5
    assert r.remaining == 4


def test_requests_up_to_limit_are_allowed(rl):
    for i in range(10):
        r = rl.check("ip:burst", limit=10, window_seconds=60)
        assert r.allowed is True, f"Request {i+1} should be allowed"


def test_request_at_limit_is_rejected(rl):
    for _ in range(5):
        rl.check("ip:x", limit=5, window_seconds=60)
    r = rl.check("ip:x", limit=5, window_seconds=60)
    assert r.allowed is False
    assert r.remaining == 0


def test_rejected_request_does_not_consume_quota(rl):
    for _ in range(3):
        rl.check("ip:y", limit=3, window_seconds=60)

    # Hit limit — reject
    r1 = rl.check("ip:y", limit=3, window_seconds=60)
    assert r1.allowed is False

    # Another reject (quota still at 3, not 4)
    r2 = rl.check("ip:y", limit=3, window_seconds=60)
    assert r2.allowed is False


def test_limit_of_one_allows_exactly_one(rl):
    r1 = rl.check("ip:one", limit=1, window_seconds=60)
    assert r1.allowed is True
    r2 = rl.check("ip:one", limit=1, window_seconds=60)
    assert r2.allowed is False


def test_different_keys_are_independent(rl):
    for _ in range(3):
        rl.check("ip:a", limit=3, window_seconds=60)

    # ip:a is at limit
    assert rl.check("ip:a", limit=3, window_seconds=60).allowed is False
    # ip:b has a clean slate
    assert rl.check("ip:b", limit=3, window_seconds=60).allowed is True


# ── Result fields ─────────────────────────────────────────────────────────────

def test_remaining_decrements(rl):
    r1 = rl.check("ip:rem", limit=5, window_seconds=60)
    assert r1.remaining == 4
    r2 = rl.check("ip:rem", limit=5, window_seconds=60)
    assert r2.remaining == 3


def test_remaining_zero_on_last_allowed(rl):
    for _ in range(4):
        rl.check("ip:last", limit=5, window_seconds=60)
    r = rl.check("ip:last", limit=5, window_seconds=60)
    assert r.allowed is True
    assert r.remaining == 0


def test_reset_in_is_positive(rl):
    rl.check("ip:rst", limit=1, window_seconds=10)
    r = rl.check("ip:rst", limit=1, window_seconds=10)
    assert r.allowed is False
    assert 0 <= r.reset_in <= 10


def test_result_is_correct_type(rl):
    r = rl.check("ip:type", limit=5, window_seconds=60)
    assert isinstance(r, RateLimitResult)


# ── Sliding window expiry ─────────────────────────────────────────────────────

def test_window_expiry_resets_count(redis):
    """Manually inject old timestamps so they fall outside the window."""
    rl = SlidingWindowRateLimiter(redis)
    key = "rg:rl:ip:expire"
    now = time.time()

    # Inject 3 timestamps that are 120 seconds old (outside a 60s window)
    for i in range(3):
        ts = now - 120 - i
        redis.zadd(key, {str(ts): ts})

    # These old entries should be pruned; new request should be allowed
    r = rl.check("ip:expire", limit=3, window_seconds=60)
    assert r.allowed is True
    assert r.remaining == 2   # 1 new entry added, 2 slots remain


# ── Large limit values ────────────────────────────────────────────────────────

def test_large_limit(rl):
    for _ in range(100):
        r = rl.check("ip:large", limit=100_000, window_seconds=3600)
        assert r.allowed is True


def test_limit_boundary_exactly(rl):
    """Exactly limit requests → all allowed; limit+1 → rejected."""
    limit = 7
    for i in range(limit):
        r = rl.check("ip:exact", limit=limit, window_seconds=60)
        assert r.allowed is True, f"Request {i+1} should be allowed (limit={limit})"
    r = rl.check("ip:exact", limit=limit, window_seconds=60)
    assert r.allowed is False


# ── Global key ────────────────────────────────────────────────────────────────

def test_global_key_shared_across_logical_ips(rl):
    """A 'global' key is shared — hits from any IP count against the same quota."""
    for _ in range(5):
        rl.check("global", limit=5, window_seconds=60)

    r = rl.check("global", limit=5, window_seconds=60)
    assert r.allowed is False
