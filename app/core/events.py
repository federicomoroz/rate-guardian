"""
Domain events emitted by the gateway pipeline.

S — each event carries only the data relevant to one thing that happened.
O — add new event types here; existing consumers are unaffected.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestBlocked:
    """Fired when a request is rejected before reaching the upstream."""
    client_ip:   str
    method:      str
    path:        str
    upstream:    str
    status_code: int
    latency_ms:  float
    reason:      str   # "rate_limit" | "circuit_breaker" | "upstream_error"


@dataclass(frozen=True)
class RequestForwarded:
    """Fired when a request was successfully proxied to the upstream."""
    client_ip:   str
    method:      str
    path:        str
    upstream:    str
    status_code: int
    latency_ms:  float
