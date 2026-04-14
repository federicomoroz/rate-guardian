import logging
import time

import redis

from app.core.config import (
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    CIRCUIT_BREAKER_RECOVERY_SECONDS,
)
from app.models.services.interfaces import CircuitBreakerBase, CircuitState

logger = logging.getLogger(__name__)

_PREFIX    = "rg:cb:"
_CLOSED    = "CLOSED"
_OPEN      = "OPEN"
_HALF_OPEN = "HALF_OPEN"


class RedisCircuitBreaker(CircuitBreakerBase):
    """
    Three-state circuit breaker with state persisted in Redis.

    State machine:
        CLOSED    → OPEN      after FAILURE_THRESHOLD consecutive failures
        OPEN      → HALF_OPEN after RECOVERY_SECONDS
        HALF_OPEN → CLOSED    on next success
        HALF_OPEN → OPEN      on next failure

    S — responsible only for circuit state transitions.
    L — fully substitutable for CircuitBreakerBase; state mutations
        are always consistent (CLOSED/OPEN/HALF_OPEN only).
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._r         = redis_client
        self._threshold = CIRCUIT_BREAKER_FAILURE_THRESHOLD
        self._recovery  = CIRCUIT_BREAKER_RECOVERY_SECONDS

    # ── Keys ─────────────────────────────────────────────────────────────────

    def _state_key(self, up: str)    -> str: return f"{_PREFIX}{up}:state"
    def _failures_key(self, up: str) -> str: return f"{_PREFIX}{up}:failures"
    def _opens_at_key(self, up: str) -> str: return f"{_PREFIX}{up}:opens_at"

    # ── CircuitBreakerBase ────────────────────────────────────────────────────

    def can_request(self, upstream: str) -> bool:
        state = self._r.get(self._state_key(upstream)) or _CLOSED

        if state == _CLOSED:
            return True

        if state == _OPEN:
            opens_at = float(self._r.get(self._opens_at_key(upstream)) or 0)
            if time.time() >= opens_at:
                self._transition(upstream, _HALF_OPEN)
                logger.info("Circuit %s -> HALF_OPEN", upstream)
                return True
            return False

        # HALF_OPEN — allow exactly one probe
        return True

    def record_success(self, upstream: str) -> None:
        state = self._r.get(self._state_key(upstream)) or _CLOSED
        if state in (_HALF_OPEN, _OPEN):
            self._transition(upstream, _CLOSED)
            logger.info("Circuit %s -> CLOSED", upstream)

    def record_failure(self, upstream: str) -> None:
        state    = self._r.get(self._state_key(upstream)) or _CLOSED
        failures = self._r.incr(self._failures_key(upstream))

        if state == _HALF_OPEN or failures >= self._threshold:
            opens_at = time.time() + self._recovery
            self._r.set(self._opens_at_key(upstream), opens_at)
            self._transition(upstream, _OPEN)
            logger.warning("Circuit %s -> OPEN (failures=%d)", upstream, failures)

    def get_state(self, upstream: str) -> CircuitState:
        state    = self._r.get(self._state_key(upstream)) or _CLOSED
        failures = int(self._r.get(self._failures_key(upstream)) or 0)
        opens_at_raw = self._r.get(self._opens_at_key(upstream))
        opens_at = float(opens_at_raw) if opens_at_raw else None
        return CircuitState(upstream=upstream, state=state, failures=failures, opens_at=opens_at)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _transition(self, upstream: str, new_state: str) -> None:
        self._r.set(self._state_key(upstream), new_state)
        if new_state == _CLOSED:
            self._r.delete(self._failures_key(upstream))
            self._r.delete(self._opens_at_key(upstream))
