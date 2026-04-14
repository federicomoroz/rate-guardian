"""
Unit tests — GatewayPipeline and individual pipeline steps.
Uses mocks for all external dependencies to test orchestration logic in isolation.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.responses import JSONResponse, Response

from app.core.event_manager import EventManager
from app.core.events import RequestBlocked, RequestForwarded
from app.controllers.pipeline import (
    PipelineContext,
    RateLimitStep,
    CircuitBreakerStep,
    ProxyStep,
    GatewayPipeline,
    _upstream_host,
)
from app.models.services.interfaces import RateLimitResult, CircuitState


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ctx(**kwargs) -> PipelineContext:
    mock_request = MagicMock()
    mock_request.method = "GET"
    defaults = dict(
        request=mock_request,
        upstream_url="http://api.test/data",
        client_ip="1.2.3.4",
        path="/proxy/http://api.test/data",
        upstream_host="api.test",
        start_time=0.0,
        db=MagicMock(),
    )
    return PipelineContext(**{**defaults, **kwargs})


def _rl_allowed(limit=10, remaining=9, reset_in=60) -> MagicMock:
    rl = MagicMock()
    rl.check.return_value = RateLimitResult(
        allowed=True, limit=limit, remaining=remaining, reset_in=reset_in
    )
    return rl


def _rl_rejected(limit=10, reset_in=5) -> MagicMock:
    rl = MagicMock()
    rl.check.return_value = RateLimitResult(
        allowed=False, limit=limit, remaining=0, reset_in=reset_in
    )
    return rl


# ── _upstream_host helper ─────────────────────────────────────────────────────

def test_upstream_host_parses_netloc():
    assert _upstream_host("http://api.example.com/path") == "api.example.com"


def test_upstream_host_with_port():
    assert _upstream_host("http://localhost:8000/api") == "localhost:8000"


def test_upstream_host_fallback():
    assert _upstream_host("just-a-string") == "just-a-string"


def test_upstream_host_https():
    assert _upstream_host("https://secure.api.io/v2/users") == "secure.api.io"


# ── RateLimitStep ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_no_rule_returns_none():
    em = EventManager()
    rl = MagicMock()
    step = RateLimitStep(rl, em)
    ctx = _ctx()

    with patch("app.controllers.pipeline.RuleRepository.match", return_value=None):
        result = await step.execute(ctx)

    assert result is None
    rl.check.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_allowed_returns_none():
    em = EventManager()
    rule = MagicMock(key_type="ip", limit=10, window_seconds=60)
    step = RateLimitStep(_rl_allowed(), em)

    with patch("app.controllers.pipeline.RuleRepository.match", return_value=rule), \
         patch("app.core.redis_client.get_redis", return_value=MagicMock()):
        result = await step.execute(_ctx())

    assert result is None


@pytest.mark.asyncio
async def test_rate_limit_rejected_returns_429():
    em = EventManager()
    rule = MagicMock(key_type="ip", limit=5, window_seconds=60)
    step = RateLimitStep(_rl_rejected(), em)

    with patch("app.controllers.pipeline.RuleRepository.match", return_value=rule), \
         patch("app.core.redis_client.get_redis", return_value=MagicMock()):
        result = await step.execute(_ctx())

    assert isinstance(result, JSONResponse)
    assert result.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_emits_blocked_event():
    em = EventManager()
    received = []
    em.subscribe(RequestBlocked, received.append)

    rule = MagicMock(key_type="ip", limit=1, window_seconds=60)
    step = RateLimitStep(_rl_rejected(), em)

    with patch("app.controllers.pipeline.RuleRepository.match", return_value=rule), \
         patch("app.core.redis_client.get_redis", return_value=MagicMock()):
        await step.execute(_ctx(client_ip="9.9.9.9"))

    assert len(received) == 1
    assert received[0].reason == "rate_limit"
    assert received[0].status_code == 429
    assert received[0].client_ip == "9.9.9.9"


@pytest.mark.asyncio
async def test_rate_limit_global_key():
    em = EventManager()
    rl = _rl_allowed()
    rule = MagicMock(key_type="global", limit=10, window_seconds=60)
    step = RateLimitStep(rl, em)

    with patch("app.controllers.pipeline.RuleRepository.match", return_value=rule), \
         patch("app.core.redis_client.get_redis", return_value=MagicMock()):
        await step.execute(_ctx(client_ip="5.5.5.5"))

    # With global key_type, rl.check should be called with "global", not the IP
    rl.check.assert_called_once_with("global", 10, 60)


@pytest.mark.asyncio
async def test_rate_limit_ip_key():
    em = EventManager()
    rl = _rl_allowed()
    rule = MagicMock(key_type="ip", limit=10, window_seconds=60)
    step = RateLimitStep(rl, em)

    with patch("app.controllers.pipeline.RuleRepository.match", return_value=rule), \
         patch("app.core.redis_client.get_redis", return_value=MagicMock()):
        await step.execute(_ctx(client_ip="7.7.7.7"))

    rl.check.assert_called_once_with("7.7.7.7", 10, 60)


# ── CircuitBreakerStep ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_circuit_closed_returns_none():
    em = EventManager()
    cb = MagicMock()
    cb.can_request.return_value = True
    step = CircuitBreakerStep(cb, em)
    result = await step.execute(_ctx())
    assert result is None


@pytest.mark.asyncio
async def test_circuit_open_returns_503():
    em = EventManager()
    cb = MagicMock()
    cb.can_request.return_value = False
    step = CircuitBreakerStep(cb, em)
    result = await step.execute(_ctx())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 503


@pytest.mark.asyncio
async def test_circuit_open_emits_blocked_event():
    em = EventManager()
    received = []
    em.subscribe(RequestBlocked, received.append)

    cb = MagicMock()
    cb.can_request.return_value = False
    step = CircuitBreakerStep(cb, em)
    await step.execute(_ctx(client_ip="3.3.3.3", upstream_host="api.bad"))

    assert len(received) == 1
    assert received[0].reason == "circuit_breaker"
    assert received[0].status_code == 503
    assert received[0].upstream == "api.bad"


@pytest.mark.asyncio
async def test_circuit_closed_no_event():
    em = EventManager()
    received = []
    em.subscribe(RequestBlocked, received.append)

    cb = MagicMock()
    cb.can_request.return_value = True
    step = CircuitBreakerStep(cb, em)
    await step.execute(_ctx())

    assert received == []


# ── ProxyStep ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_proxy_success_sets_ctx_response():
    em = EventManager()
    upstream_resp = MagicMock()
    upstream_resp.status_code = 200
    upstream_resp.content = b'{"ok": true}'
    upstream_resp.headers = {"content-type": "application/json"}

    px = MagicMock()
    px.forward = AsyncMock(return_value=upstream_resp)

    cb = MagicMock()
    step = ProxyStep(px, cb, em)
    ctx = _ctx()

    result = await step.execute(ctx)

    assert result is None
    assert ctx.response is not None
    assert ctx.response.status_code == 200


@pytest.mark.asyncio
async def test_proxy_success_records_cb_success():
    em = EventManager()
    upstream_resp = MagicMock(status_code=200, content=b"", headers={})
    px = MagicMock()
    px.forward = AsyncMock(return_value=upstream_resp)
    cb = MagicMock()
    step = ProxyStep(px, cb, em)

    await step.execute(_ctx(upstream_host="api.ok"))
    cb.record_success.assert_called_once_with("api.ok")


@pytest.mark.asyncio
async def test_proxy_success_emits_forwarded_event():
    em = EventManager()
    received = []
    em.subscribe(RequestForwarded, received.append)

    upstream_resp = MagicMock(status_code=201, content=b"created", headers={})
    px = MagicMock()
    px.forward = AsyncMock(return_value=upstream_resp)
    cb = MagicMock()
    step = ProxyStep(px, cb, em)

    await step.execute(_ctx(client_ip="8.8.8.8", upstream_host="backend.io"))

    assert len(received) == 1
    assert received[0].status_code == 201
    assert received[0].client_ip == "8.8.8.8"
    assert received[0].upstream == "backend.io"


@pytest.mark.asyncio
async def test_proxy_failure_returns_502():
    em = EventManager()
    px = MagicMock()
    px.forward = AsyncMock(side_effect=Exception("Connection refused"))
    cb = MagicMock()
    step = ProxyStep(px, cb, em)
    result = await step.execute(_ctx())
    assert isinstance(result, JSONResponse)
    assert result.status_code == 502


@pytest.mark.asyncio
async def test_proxy_failure_records_cb_failure():
    em = EventManager()
    px = MagicMock()
    px.forward = AsyncMock(side_effect=Exception("timeout"))
    cb = MagicMock()
    step = ProxyStep(px, cb, em)
    await step.execute(_ctx(upstream_host="api.broken"))
    cb.record_failure.assert_called_once_with("api.broken")


@pytest.mark.asyncio
async def test_proxy_failure_emits_blocked_event():
    em = EventManager()
    received = []
    em.subscribe(RequestBlocked, received.append)

    px = MagicMock()
    px.forward = AsyncMock(side_effect=Exception("boom"))
    cb = MagicMock()
    step = ProxyStep(px, cb, em)
    await step.execute(_ctx())

    assert len(received) == 1
    assert received[0].reason == "upstream_error"
    assert received[0].status_code == 502


@pytest.mark.asyncio
async def test_proxy_strips_hop_by_hop_headers():
    em = EventManager()
    upstream_resp = MagicMock()
    upstream_resp.status_code = 200
    upstream_resp.content = b"body"
    upstream_resp.headers = {
        "content-type": "text/plain",
        "content-encoding": "gzip",
        "transfer-encoding": "chunked",
        "connection": "keep-alive",
    }
    px = MagicMock()
    px.forward = AsyncMock(return_value=upstream_resp)
    cb = MagicMock()
    step = ProxyStep(px, cb, em)
    ctx = _ctx()
    await step.execute(ctx)

    resp_headers = dict(ctx.response.headers)
    assert "content-encoding" not in resp_headers
    assert "transfer-encoding" not in resp_headers
    assert "connection" not in resp_headers
    assert "content-type" in resp_headers


# ── GatewayPipeline ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_short_circuits_on_first_response():
    ctx = _ctx()
    step1 = MagicMock()
    step1.execute = AsyncMock(return_value=JSONResponse(status_code=429, content={}))
    step2 = MagicMock()
    step2.execute = AsyncMock(return_value=None)

    pipeline = GatewayPipeline([step1, step2])
    result = await pipeline.run(ctx)

    step1.execute.assert_called_once_with(ctx)
    step2.execute.assert_not_called()
    assert result.status_code == 429


@pytest.mark.asyncio
async def test_pipeline_runs_all_steps_when_none():
    ctx = _ctx()
    final_resp = Response(content=b"ok", status_code=200)
    ctx.response = final_resp

    step1 = MagicMock()
    step1.execute = AsyncMock(return_value=None)
    step2 = MagicMock()
    step2.execute = AsyncMock(return_value=None)

    pipeline = GatewayPipeline([step1, step2])
    result = await pipeline.run(ctx)

    step1.execute.assert_called_once()
    step2.execute.assert_called_once()
    assert result is final_resp


@pytest.mark.asyncio
async def test_pipeline_fallback_when_no_response():
    ctx = _ctx()
    ctx.response = None

    step = MagicMock()
    step.execute = AsyncMock(return_value=None)

    pipeline = GatewayPipeline([step])
    result = await pipeline.run(ctx)
    assert result.status_code == 500


@pytest.mark.asyncio
async def test_pipeline_empty_steps_fallback():
    ctx = _ctx()
    ctx.response = None
    pipeline = GatewayPipeline([])
    result = await pipeline.run(ctx)
    assert result.status_code == 500
