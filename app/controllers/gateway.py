"""
GatewayController — thin orchestrator that wires up and runs the request pipeline.

S — creates a PipelineContext per request and delegates to GatewayPipeline.
D — depends only on pipeline abstractions; concrete steps are injected.
"""

import time
import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.controllers.pipeline import (
    CircuitBreakerStep,
    GatewayPipeline,
    PipelineContext,
    ProxyStep,
    RateLimitStep,
    _upstream_host,
)
from app.core.event_manager import EventManager
from app.models.services.interfaces import CircuitBreakerBase, ProxyBase, RateLimiterBase

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Gateway"])


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host or "unknown")


class GatewayController:
    """
    Entry point for every proxied request.
    Builds a PipelineContext and delegates execution to a composed GatewayPipeline.

    S — responsible only for request context setup and pipeline invocation.
    D — all collaborators are injected as abstractions.
    """

    def __init__(
        self,
        rate_limiter:    RateLimiterBase,
        circuit_breaker: CircuitBreakerBase,
        proxy:           ProxyBase,
        events:          EventManager,
    ) -> None:
        self._pipeline = GatewayPipeline([
            RateLimitStep(rate_limiter, events),
            CircuitBreakerStep(circuit_breaker, events),
            ProxyStep(proxy, circuit_breaker, events),
        ])

    async def handle(self, upstream_url: str, request: Request, db: Session) -> Response:
        ctx = PipelineContext(
            request=request,
            upstream_url=upstream_url,
            client_ip=_get_client_ip(request),
            path=request.url.path,
            upstream_host=_upstream_host(upstream_url),
            start_time=time.monotonic(),
            db=db,
        )
        return await self._pipeline.run(ctx)


# ── FastAPI route ─────────────────────────────────────────────────────────────

@router.api_route(
    "/proxy/{upstream_url:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    summary="Forward a request to the upstream URL",
    description=(
        "Pass the full upstream URL as a path segment.\n\n"
        "Example: `GET /proxy/https://jsonplaceholder.typicode.com/posts`"
    ),
)
async def proxy(
    upstream_url: str,
    request:      Request,
    db:           Session = Depends(get_db),
):
    controller: GatewayController = request.app.state.gateway
    return await controller.handle(upstream_url, request, db)
