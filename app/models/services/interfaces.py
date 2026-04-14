"""
Abstract base classes for every swappable service.

I — each interface exposes only the methods its consumers need.
D — all high-level modules (controllers, gateway) depend on these
    abstractions, never on concrete implementations.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx
from fastapi import Request


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RateLimitResult:
    """
    Outcome of a rate-limit check.
    S — pure data; carries the decision and context, nothing else.
    """
    allowed:    bool
    limit:      int
    remaining:  int
    reset_in:   int   # seconds until the window resets


@dataclass(frozen=True)
class CircuitState:
    """Current state of a circuit breaker for one upstream."""
    upstream:  str
    state:     str          # CLOSED | OPEN | HALF_OPEN
    failures:  int
    opens_at:  float | None  # epoch when OPEN expires


# ---------------------------------------------------------------------------
# Abstractions
# ---------------------------------------------------------------------------

class RateLimiterBase(ABC):
    """
    Contract for any rate-limiting strategy.
    I — one method only; implementations never carry unrelated concerns.
    """

    @abstractmethod
    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        """
        Evaluate whether *key* is within its quota.
        Must be idempotent on rejection (i.e. a rejected call must NOT
        consume quota).
        """


class CircuitBreakerBase(ABC):
    """
    Contract for circuit-breaker state management.
    I — three focused methods that map 1-to-1 to the state machine events.
    """

    @abstractmethod
    def can_request(self, upstream: str) -> bool:
        """True when the circuit allows a request to be forwarded."""

    @abstractmethod
    def record_success(self, upstream: str) -> None:
        """Notify the breaker that the last request to *upstream* succeeded."""

    @abstractmethod
    def record_failure(self, upstream: str) -> None:
        """Notify the breaker that the last request to *upstream* failed."""

    @abstractmethod
    def get_state(self, upstream: str) -> CircuitState:
        """Return the current state for *upstream* (for dashboard display)."""


class ProxyBase(ABC):
    """
    Contract for forwarding a request to an upstream and returning its response.
    I — one method; transport details are entirely encapsulated.
    """

    @abstractmethod
    async def forward(self, request: Request, upstream_url: str) -> httpx.Response:
        """
        Replay *request* against *upstream_url* and return the raw response.
        Raises httpx.HTTPError on network/upstream failure.
        """
