"""
Unit tests — RedisCircuitBreaker.
Covers: initial state CLOSED, failure counting, CLOSED→OPEN transition,
        OPEN blocks requests, OPEN→HALF_OPEN after recovery, HALF_OPEN→CLOSED
        on success, HALF_OPEN→OPEN on failure, get_state, multi-upstream isolation.
"""

import time
import pytest
import fakeredis

from unittest.mock import patch
from app.models.services.circuit_breaker import RedisCircuitBreaker
from app.models.services.interfaces import CircuitState


THRESHOLD = 5   # matches config default
RECOVERY  = 30  # matches config default


@pytest.fixture()
def redis():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def cb(redis):
    return RedisCircuitBreaker(redis)


# ── Initial state (CLOSED) ────────────────────────────────────────────────────

def test_new_upstream_can_request(cb):
    assert cb.can_request("api.new") is True


def test_initial_state_is_closed(cb):
    s = cb.get_state("api.new")
    assert s.state == "CLOSED"
    assert s.failures == 0
    assert s.opens_at is None


def test_success_on_closed_is_noop(cb):
    cb.record_success("api.ok")
    assert cb.get_state("api.ok").state == "CLOSED"


# ── Failure counting → OPEN ───────────────────────────────────────────────────

def test_failures_below_threshold_stay_closed(cb):
    for _ in range(THRESHOLD - 1):
        cb.record_failure("api.almost")
    assert cb.can_request("api.almost") is True
    assert cb.get_state("api.almost").state == "CLOSED"


def test_threshold_failures_open_circuit(cb):
    for _ in range(THRESHOLD):
        cb.record_failure("api.trip")
    s = cb.get_state("api.trip")
    assert s.state == "OPEN"
    assert cb.can_request("api.trip") is False


def test_failure_count_increments(cb):
    cb.record_failure("api.cnt")
    cb.record_failure("api.cnt")
    assert cb.get_state("api.cnt").failures == 2


def test_one_more_than_threshold_still_open(cb):
    for _ in range(THRESHOLD + 3):
        cb.record_failure("api.over")
    assert cb.can_request("api.over") is False


# ── OPEN → HALF_OPEN after recovery ──────────────────────────────────────────

def test_open_circuit_blocks_before_recovery(cb):
    for _ in range(THRESHOLD):
        cb.record_failure("api.wait")
    # Recovery hasn't elapsed
    assert cb.can_request("api.wait") is False


def test_open_becomes_half_open_after_recovery(cb, redis):
    upstream = "api.recover"
    for _ in range(THRESHOLD):
        cb.record_failure(upstream)

    # Force opens_at to the past
    opens_at_key = f"rg:cb:{upstream}:opens_at"
    redis.set(opens_at_key, time.time() - 1)

    assert cb.can_request(upstream) is True
    assert cb.get_state(upstream).state == "HALF_OPEN"


def test_open_still_blocked_before_recovery_time(cb, redis):
    upstream = "api.notyet"
    for _ in range(THRESHOLD):
        cb.record_failure(upstream)

    # opens_at = far future
    opens_at_key = f"rg:cb:{upstream}:opens_at"
    redis.set(opens_at_key, time.time() + 9999)

    assert cb.can_request(upstream) is False


# ── HALF_OPEN → CLOSED on success ────────────────────────────────────────────

def test_half_open_success_closes_circuit(cb, redis):
    upstream = "api.heal"
    for _ in range(THRESHOLD):
        cb.record_failure(upstream)
    redis.set(f"rg:cb:{upstream}:opens_at", time.time() - 1)

    cb.can_request(upstream)           # transitions to HALF_OPEN
    cb.record_success(upstream)

    assert cb.get_state(upstream).state == "CLOSED"
    assert cb.can_request(upstream) is True


def test_closed_after_heal_clears_failure_count(cb, redis):
    upstream = "api.healcount"
    for _ in range(THRESHOLD):
        cb.record_failure(upstream)
    redis.set(f"rg:cb:{upstream}:opens_at", time.time() - 1)
    cb.can_request(upstream)
    cb.record_success(upstream)
    assert cb.get_state(upstream).failures == 0


# ── HALF_OPEN → OPEN on failure ───────────────────────────────────────────────

def test_half_open_failure_reopens_circuit(cb, redis):
    upstream = "api.fail_probe"
    for _ in range(THRESHOLD):
        cb.record_failure(upstream)
    redis.set(f"rg:cb:{upstream}:opens_at", time.time() - 1)

    cb.can_request(upstream)           # transitions to HALF_OPEN
    cb.record_failure(upstream)        # probe failed → back to OPEN

    assert cb.get_state(upstream).state == "OPEN"
    assert cb.can_request(upstream) is False


# ── Multi-upstream isolation ───────────────────────────────────────────────────

def test_upstreams_are_independent(cb):
    for _ in range(THRESHOLD):
        cb.record_failure("api.bad")

    assert cb.can_request("api.bad") is False
    assert cb.can_request("api.good") is True
    assert cb.get_state("api.good").state == "CLOSED"


def test_many_upstreams_independent(cb):
    upstreams = [f"api.svc{i}" for i in range(10)]
    for up in upstreams[:5]:
        for _ in range(THRESHOLD):
            cb.record_failure(up)

    for up in upstreams[:5]:
        assert cb.can_request(up) is False
    for up in upstreams[5:]:
        assert cb.can_request(up) is True


# ── get_state ────────────────────────────────────────────────────────────────

def test_get_state_returns_circuit_state_type(cb):
    s = cb.get_state("api.x")
    assert isinstance(s, CircuitState)
    assert s.upstream == "api.x"


def test_get_state_after_open_has_opens_at(cb):
    upstream = "api.opens"
    t_before = time.time()
    for _ in range(THRESHOLD):
        cb.record_failure(upstream)
    t_after = time.time()

    s = cb.get_state(upstream)
    assert s.opens_at is not None
    assert t_before <= s.opens_at - RECOVERY <= t_after + 1


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_success_record_on_unknown_upstream_is_safe(cb):
    cb.record_success("api.never_seen")


def test_failure_threshold_of_one(redis):
    """Edge case: threshold=1 should open on first failure."""
    from app.core import config as cfg
    original = cfg.CIRCUIT_BREAKER_FAILURE_THRESHOLD
    cfg.CIRCUIT_BREAKER_FAILURE_THRESHOLD = 1

    cb = RedisCircuitBreaker(redis)
    cb._threshold = 1
    cb.record_failure("api.threshold1")
    assert cb.can_request("api.threshold1") is False

    cfg.CIRCUIT_BREAKER_FAILURE_THRESHOLD = original


def test_circuit_breaker_state_is_frozen():
    s = CircuitState(upstream="x", state="CLOSED", failures=0, opens_at=None)
    with pytest.raises((AttributeError, TypeError)):
        s.state = "OPEN"
