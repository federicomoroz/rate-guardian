import logging
from contextlib import asynccontextmanager

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import Request

from app.core.config import LOG_RETENTION_DAYS
from app.core.database import Base, SessionLocal, engine, get_db
from app.core.event_manager import EventManager
from app.core.redis_client import get_redis
from app.controllers import dashboard, gateway, rules
from app.models.services.circuit_breaker import RedisCircuitBreaker
from app.models.services.log_listener import LogListener
from app.models.services.log_service import LogService
from app.models.services.proxy import HttpxProxyService
from app.models.services.rate_limiter import SlidingWindowRateLimiter

logging.basicConfig(level=logging.INFO)

Base.metadata.create_all(bind=engine)

scheduler = BackgroundScheduler()


def _purge_logs_job():
    db = SessionLocal()
    try:
        LogService.purge_old(db, LOG_RETENTION_DAYS)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Shared transport layer ────────────────────────────────────────────────
    http_client  = httpx.AsyncClient(timeout=15.0)
    redis_client = get_redis()

    # ── Event bus ────────────────────────────────────────────────────────────
    events = EventManager()

    # ── Concrete services ────────────────────────────────────────────────────
    rate_limiter    = SlidingWindowRateLimiter(redis_client)
    circuit_breaker = RedisCircuitBreaker(redis_client)
    proxy           = HttpxProxyService(http_client)

    # ── Wire log listener (subscribes itself to the event bus) ───────────────
    LogListener(db_factory=SessionLocal, events=events)

    # ── Inject into app.state ────────────────────────────────────────────────
    app.state.rate_limiter    = rate_limiter
    app.state.circuit_breaker = circuit_breaker
    app.state.proxy           = proxy
    app.state.events          = events

    # ── Compose and store GatewayController ──────────────────────────────────
    from app.controllers.gateway import GatewayController
    app.state.gateway = GatewayController(
        rate_limiter=rate_limiter,
        circuit_breaker=circuit_breaker,
        proxy=proxy,
        events=events,
    )

    # ── Log-purge scheduler ───────────────────────────────────────────────────
    scheduler.add_job(_purge_logs_job, "interval", hours=24, id="purge_logs", replace_existing=True)
    scheduler.start()

    yield

    await http_client.aclose()
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Rate Guardian",
    description=(
        "API Gateway with sliding-window rate limiting, Redis-backed circuit breaker, "
        "transparent HTTP proxy, and real-time dashboard."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from app.models.repositories.log_repository import LogRepository
from app.views.templates.dashboard import render_dashboard


@app.get("/", include_in_schema=False)
async def _root(request: Request, db: Session = Depends(get_db)):
    stats      = LogRepository.stats(db, since_minutes=5)
    top_ips    = LogRepository.top_ips(db, limit=5, since_minutes=60)
    recent     = LogRepository.get_recent(db, limit=10)
    saturation = (
        round(stats["blocked_requests"] / stats["total_requests"] * 100, 1)
        if stats["total_requests"] > 0 else 0.0
    )
    return HTMLResponse(render_dashboard(stats, top_ips, recent, saturation))


for router in [gateway.router, rules.router, dashboard.router]:
    app.include_router(router)
