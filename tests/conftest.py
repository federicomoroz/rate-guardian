"""
Shared test fixtures.
All tests use:
  - SQLite in-memory database with StaticPool (thread-safe, all connections share one DB)
  - FakeRedis with decode_responses=True (matches real Redis client config)
  - FastAPI ASGI test client (no real network)
"""

import asyncio
import pytest
import fakeredis

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from httpx import AsyncClient, ASGITransport

from app.core.database import Base
from app.core.event_manager import EventManager
from app.models.services.circuit_breaker import RedisCircuitBreaker
from app.models.services.rate_limiter import SlidingWindowRateLimiter
from app.models.services.log_listener import LogListener


# ── Database ──────────────────────────────────────────────────────────────────
# StaticPool: all SQLAlchemy connections share a single underlying SQLite
# connection, so the in-memory database is visible across all threads
# (including FastAPI's sync thread-pool executor).

@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture()
def db_factory(db_engine):
    Session = sessionmaker(bind=db_engine)
    return Session


# ── Redis ─────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis(decode_responses=True)


# ── Services ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def rate_limiter(fake_redis):
    return SlidingWindowRateLimiter(fake_redis)


@pytest.fixture()
def circuit_breaker(fake_redis):
    return RedisCircuitBreaker(fake_redis)


@pytest.fixture()
def event_manager():
    return EventManager()


# ── Full ASGI app with isolated DB + FakeRedis ────────────────────────────────

@pytest.fixture()
def test_app(db_engine, fake_redis):
    """
    Returns a fully-wired FastAPI app backed by in-memory SQLite + FakeRedis.
    Patches module-level singletons so every dependency resolves to test state.
    """
    import app.core.database as db_module
    import app.core.redis_client as redis_module

    # ── Patch DB ──────────────────────────────────────────────────────────────
    _orig_engine      = db_module.engine
    _orig_session_cls = db_module.SessionLocal

    TestSession = sessionmaker(bind=db_engine)
    db_module.engine       = db_engine
    db_module.SessionLocal = TestSession
    Base.metadata.create_all(bind=db_engine)

    # ── Patch Redis singleton ─────────────────────────────────────────────────
    _orig_redis     = redis_module._client
    _orig_get_redis = redis_module.get_redis
    redis_module._client    = fake_redis
    redis_module.get_redis  = lambda: fake_redis

    # ── Import the shared app instance ────────────────────────────────────────
    from app.main import app as fastapi_app

    # ── Wire test services into app.state ─────────────────────────────────────
    from app.models.services.rate_limiter import SlidingWindowRateLimiter
    from app.models.services.circuit_breaker import RedisCircuitBreaker
    from app.models.services.proxy import HttpxProxyService
    from app.controllers.gateway import GatewayController
    import httpx

    rl          = SlidingWindowRateLimiter(fake_redis)
    cb          = RedisCircuitBreaker(fake_redis)
    ev          = EventManager()
    http_client = httpx.AsyncClient(timeout=15.0)
    px          = HttpxProxyService(http_client)

    LogListener(db_factory=TestSession, events=ev)

    fastapi_app.state.rate_limiter    = rl
    fastapi_app.state.circuit_breaker = cb
    fastapi_app.state.proxy           = px
    fastapi_app.state.events          = ev
    fastapi_app.state.gateway = GatewayController(
        rate_limiter=rl,
        circuit_breaker=cb,
        proxy=px,
        events=ev,
    )

    # ── Override get_db dependency (every endpoint sees TestSession) ───────────
    from app.core.database import get_db

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db

    yield fastapi_app

    # ── Teardown ──────────────────────────────────────────────────────────────
    fastapi_app.dependency_overrides.clear()
    db_module.engine       = _orig_engine
    db_module.SessionLocal = _orig_session_cls
    redis_module._client   = _orig_redis
    redis_module.get_redis = _orig_get_redis
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(http_client.aclose())
        else:
            loop.run_until_complete(http_client.aclose())
    except Exception:
        pass


@pytest.fixture()
async def client(test_app):
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as c:
        yield c
