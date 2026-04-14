"""
Gateway request pipeline built from composable steps.

S — each step has exactly one responsibility.
O — new steps can be inserted without modifying existing ones.
L — every step honours the PipelineStep protocol.
D — steps depend on abstract interfaces (RateLimiterBase, etc.), not concretions.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import app.core.redis_client as _redis_mod
from app.core.event_manager import EventManager
from app.core.events import RequestBlocked, RequestForwarded
from app.models.repositories.rule_repository import RuleRepository
from app.models.services.interfaces import CircuitBreakerBase, ProxyBase, RateLimiterBase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared context (passed by reference through every step)
# ---------------------------------------------------------------------------

@dataclass
class PipelineContext:
    """
    Mutable bag-of-state shared across all pipeline steps.
    Created once per request; steps read from and write into it.
    """
    request:       Request
    upstream_url:  str
    client_ip:     str
    path:          str
    upstream_host: str
    start_time:    float
    db:            Session
    response:      Response | None = field(default=None)


# ---------------------------------------------------------------------------
# Step protocol (Liskov: any step can replace any other in the pipeline)
# ---------------------------------------------------------------------------

class PipelineStep(Protocol):
    async def execute(self, ctx: PipelineContext) -> Response | None:
        """
        Process the context.
        Return a Response to short-circuit the pipeline (reject the request).
        Return None to let the next step run.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete steps
# ---------------------------------------------------------------------------

class RateLimitStep:
    """
    Checks the incoming request against active rate-limiting rules.
    Emits RequestBlocked if the limit is exceeded.
    """

    def __init__(self, rate_limiter: RateLimiterBase, events: EventManager) -> None:
        self._rl = rate_limiter
        self._ev = events

    async def execute(self, ctx: PipelineContext) -> Response | None:
        rule = RuleRepository.match(ctx.db, ctx.path, _redis_mod.get_redis())
        if not rule:
            return None

        rl_key = ctx.client_ip if rule.key_type == "ip" else "global"
        result = self._rl.check(rl_key, rule.limit, rule.window_seconds)

        if not result.allowed:
            latency = (time.monotonic() - ctx.start_time) * 1000
            self._ev.emit(RequestBlocked(
                client_ip=ctx.client_ip, method=ctx.request.method,
                path=ctx.path, upstream=ctx.upstream_host,
                status_code=429, latency_ms=latency, reason="rate_limit",
            ))
            return JSONResponse(
                status_code=429,
                content={
                    "error":    "Too Many Requests",
                    "limit":    result.limit,
                    "reset_in": result.reset_in,
                },
                headers={
                    "X-RateLimit-Limit":     str(result.limit),
                    "X-RateLimit-Remaining": str(result.remaining),
                    "X-RateLimit-Reset":     str(result.reset_in),
                    "Retry-After":           str(result.reset_in),
                },
            )
        return None


class CircuitBreakerStep:
    """
    Rejects requests when the upstream circuit is open.
    Emits RequestBlocked on rejection.
    """

    def __init__(self, circuit_breaker: CircuitBreakerBase, events: EventManager) -> None:
        self._cb = circuit_breaker
        self._ev = events

    async def execute(self, ctx: PipelineContext) -> Response | None:
        if not self._cb.can_request(ctx.upstream_host):
            latency = (time.monotonic() - ctx.start_time) * 1000
            self._ev.emit(RequestBlocked(
                client_ip=ctx.client_ip, method=ctx.request.method,
                path=ctx.path, upstream=ctx.upstream_host,
                status_code=503, latency_ms=latency, reason="circuit_breaker",
            ))
            return JSONResponse(
                status_code=503,
                content={"error": "Service Unavailable", "reason": "circuit_breaker_open"},
            )
        return None


class ProxyStep:
    """
    Forwards the request to the upstream and records the result.
    Emits RequestForwarded on success or RequestBlocked on upstream error.
    Updates the circuit breaker state accordingly.
    """

    def __init__(
        self,
        proxy:           ProxyBase,
        circuit_breaker: CircuitBreakerBase,
        events:          EventManager,
    ) -> None:
        self._px = proxy
        self._cb = circuit_breaker
        self._ev = events

    async def execute(self, ctx: PipelineContext) -> Response | None:
        try:
            upstream_resp = await self._px.forward(ctx.request, ctx.upstream_url)
            self._cb.record_success(ctx.upstream_host)
            latency = (time.monotonic() - ctx.start_time) * 1000

            self._ev.emit(RequestForwarded(
                client_ip=ctx.client_ip, method=ctx.request.method,
                path=ctx.path, upstream=ctx.upstream_host,
                status_code=upstream_resp.status_code, latency_ms=latency,
            ))

            headers = dict(upstream_resp.headers)
            for h in ("content-encoding", "transfer-encoding", "connection"):
                headers.pop(h, None)

            ctx.response = Response(
                content=upstream_resp.content,
                status_code=upstream_resp.status_code,
                headers=headers,
            )
            return None  # signal: pipeline complete, use ctx.response

        except Exception as exc:
            self._cb.record_failure(ctx.upstream_host)
            logger.error("Upstream error for %s: %s", ctx.upstream_url, exc)
            latency = (time.monotonic() - ctx.start_time) * 1000
            self._ev.emit(RequestBlocked(
                client_ip=ctx.client_ip, method=ctx.request.method,
                path=ctx.path, upstream=ctx.upstream_host,
                status_code=502, latency_ms=latency, reason="upstream_error",
            ))
            return JSONResponse(
                status_code=502,
                content={"error": "Bad Gateway", "detail": str(exc)},
            )


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class GatewayPipeline:
    """
    Runs steps in order; the first non-None response short-circuits the chain.
    S — knows only how to run steps; it does not implement any step logic.
    O — steps list is injected; adding steps requires no changes here.
    """

    def __init__(self, steps: list[PipelineStep]) -> None:
        self._steps = steps

    async def run(self, ctx: PipelineContext) -> Response:
        for step in self._steps:
            result = await step.execute(ctx)
            if result is not None:
                return result
        return ctx.response or JSONResponse(status_code=500, content={"error": "No response produced"})


def _upstream_host(url: str) -> str:
    return urlparse(url).netloc or url
