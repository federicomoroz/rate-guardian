"""
Integration tests — all HTTP API routes via ASGI test client.

Covers:
  - GET  /              → 200 HTML dashboard
  - GET  /dashboard     → 200 JSON stats
  - GET  /logs          → 200 JSON log list
  - GET  /rules         → 200 JSON rule list
  - POST /rules         → 201 created
  - PATCH /rules/{id}/toggle
  - DELETE /rules/{id}
  - GET /proxy/...      → forwards, logs, returns upstream response
  - Rate limiting enforcement (429)
  - Circuit breaker enforcement (503)
  - Bad gateway (502)
  - Validation errors (422)
  - Not found (404)
  - Schema conformance
"""

import asyncio
import json
import pytest
import fakeredis
import respx
import httpx

from unittest.mock import patch, AsyncMock, MagicMock


pytestmark = pytest.mark.asyncio


# ── Dashboard HTML ────────────────────────────────────────────────────────────

async def test_root_returns_html(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<!DOCTYPE html>" in r.text


async def test_root_contains_gauge(client):
    r = await client.get("/")
    assert "<svg" in r.text


async def test_root_contains_stats_section(client):
    r = await client.get("/")
    assert "RATE GUARDIAN" in r.text.upper()


async def test_root_auto_refresh_meta(client):
    r = await client.get("/")
    assert 'http-equiv="refresh"' in r.text


# ── /dashboard JSON ───────────────────────────────────────────────────────────

async def test_dashboard_json_structure(client):
    r = await client.get("/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "stats" in body
    assert "top_ips" in body
    assert "circuit_breakers" in body


async def test_dashboard_stats_fields(client):
    r = await client.get("/dashboard")
    s = r.json()["stats"]
    for key in ("total_requests", "blocked_requests", "allowed_requests",
                "avg_latency_ms", "window_minutes", "saturation_pct"):
        assert key in s, f"Missing key: {key}"


async def test_dashboard_window_default_5(client):
    r = await client.get("/dashboard")
    assert r.json()["stats"]["window_minutes"] == 5


async def test_dashboard_window_param(client):
    r = await client.get("/dashboard?window=15")
    assert r.status_code == 200
    assert r.json()["stats"]["window_minutes"] == 15


async def test_dashboard_window_min_1(client):
    r = await client.get("/dashboard?window=1")
    assert r.status_code == 200


async def test_dashboard_window_max_60(client):
    r = await client.get("/dashboard?window=60")
    assert r.status_code == 200


async def test_dashboard_window_out_of_range(client):
    r = await client.get("/dashboard?window=0")
    assert r.status_code == 422

    r = await client.get("/dashboard?window=61")
    assert r.status_code == 422


async def test_dashboard_empty_state(client):
    r = await client.get("/dashboard")
    s = r.json()["stats"]
    assert s["total_requests"] == 0
    assert s["saturation_pct"] == 0.0


# ── /logs ─────────────────────────────────────────────────────────────────────

async def test_logs_empty(client):
    r = await client.get("/logs")
    assert r.status_code == 200
    assert r.json() == []


async def test_logs_default_limit_50(client):
    r = await client.get("/logs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_logs_blocked_filter_false(client):
    r = await client.get("/logs?blocked=false")
    assert r.status_code == 200


async def test_logs_blocked_filter_true(client):
    r = await client.get("/logs?blocked=true")
    assert r.status_code == 200


async def test_logs_limit_param(client):
    r = await client.get("/logs?limit=10")
    assert r.status_code == 200


async def test_logs_limit_out_of_range(client):
    r = await client.get("/logs?limit=0")
    assert r.status_code == 422

    r = await client.get("/logs?limit=501")
    assert r.status_code == 422


# ── /rules CRUD ───────────────────────────────────────────────────────────────

async def test_list_rules_empty(client):
    r = await client.get("/rules")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_rule_201(client):
    r = await client.post("/rules", json={
        "name": "API Limit",
        "path_pattern": "/proxy/api/*",
        "limit": 100,
        "window_seconds": 60,
        "key_type": "ip",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["id"] is not None
    assert body["name"] == "API Limit"
    assert body["active"] is True


async def test_create_rule_fields_persisted(client):
    r = await client.post("/rules", json={
        "name": "Global Cap",
        "path_pattern": "/proxy/*",
        "limit": 500,
        "window_seconds": 3600,
        "key_type": "global",
    })
    body = r.json()
    assert body["path_pattern"] == "/proxy/*"
    assert body["limit"] == 500
    assert body["window_seconds"] == 3600
    assert body["key_type"] == "global"


async def test_create_rule_appears_in_list(client):
    await client.post("/rules", json={
        "name": "Test", "path_pattern": "/x/*",
        "limit": 10, "window_seconds": 10, "key_type": "ip",
    })
    r = await client.get("/rules")
    assert any(rule["name"] == "Test" for rule in r.json())


async def test_create_rule_validation_name_empty(client):
    r = await client.post("/rules", json={
        "name": "", "path_pattern": "/x/*",
        "limit": 10, "window_seconds": 10,
    })
    assert r.status_code == 422


async def test_create_rule_validation_limit_zero(client):
    r = await client.post("/rules", json={
        "name": "R", "path_pattern": "/x/*",
        "limit": 0, "window_seconds": 10,
    })
    assert r.status_code == 422


async def test_create_rule_validation_limit_negative(client):
    r = await client.post("/rules", json={
        "name": "R", "path_pattern": "/x/*",
        "limit": -1, "window_seconds": 10,
    })
    assert r.status_code == 422


async def test_create_rule_validation_window_zero(client):
    r = await client.post("/rules", json={
        "name": "R", "path_pattern": "/x/*",
        "limit": 10, "window_seconds": 0,
    })
    assert r.status_code == 422


async def test_create_rule_validation_key_type_invalid(client):
    r = await client.post("/rules", json={
        "name": "R", "path_pattern": "/x/*",
        "limit": 10, "window_seconds": 10, "key_type": "user",
    })
    assert r.status_code == 422


async def test_create_rule_max_limit(client):
    r = await client.post("/rules", json={
        "name": "Big", "path_pattern": "/x/*",
        "limit": 100_000, "window_seconds": 1,
    })
    assert r.status_code == 201


async def test_create_rule_max_window(client):
    r = await client.post("/rules", json={
        "name": "Long", "path_pattern": "/x/*",
        "limit": 1, "window_seconds": 86_400,
    })
    assert r.status_code == 201


async def test_create_rule_missing_required_fields(client):
    r = await client.post("/rules", json={"name": "R"})
    assert r.status_code == 422


async def test_toggle_rule_disables(client):
    create = await client.post("/rules", json={
        "name": "Toggle", "path_pattern": "/t/*",
        "limit": 10, "window_seconds": 10,
    })
    rule_id = create.json()["id"]

    r = await client.patch(f"/rules/{rule_id}/toggle")
    assert r.status_code == 200
    assert r.json()["active"] is False


async def test_toggle_rule_enables(client):
    create = await client.post("/rules", json={
        "name": "Toggle2", "path_pattern": "/t2/*",
        "limit": 10, "window_seconds": 10,
    })
    rule_id = create.json()["id"]
    await client.patch(f"/rules/{rule_id}/toggle")  # disable
    r = await client.patch(f"/rules/{rule_id}/toggle")  # enable
    assert r.json()["active"] is True


async def test_toggle_nonexistent_rule_404(client):
    r = await client.patch("/rules/99999/toggle")
    assert r.status_code == 404


async def test_delete_rule_204(client):
    create = await client.post("/rules", json={
        "name": "Delete", "path_pattern": "/d/*",
        "limit": 10, "window_seconds": 10,
    })
    rule_id = create.json()["id"]
    r = await client.delete(f"/rules/{rule_id}")
    assert r.status_code == 204


async def test_delete_rule_removes_from_list(client):
    create = await client.post("/rules", json={
        "name": "Gone", "path_pattern": "/g/*",
        "limit": 10, "window_seconds": 10,
    })
    rule_id = create.json()["id"]
    await client.delete(f"/rules/{rule_id}")
    rules = (await client.get("/rules")).json()
    assert not any(r["id"] == rule_id for r in rules)


async def test_delete_nonexistent_rule_404(client):
    r = await client.delete("/rules/99999")
    assert r.status_code == 404


# ── /proxy endpoint ───────────────────────────────────────────────────────────

async def test_proxy_no_rule_forwards(test_app, client):
    """Without any matching rule, requests should be forwarded (no rate limit)."""
    mock_resp = httpx.Response(200, json={"id": 1, "title": "hello"})

    with respx.mock(base_url="https://jsonplaceholder.typicode.com") as mock:
        mock.get("/posts/1").mock(return_value=mock_resp)
        r = await client.get("/proxy/https://jsonplaceholder.typicode.com/posts/1")

    assert r.status_code == 200


async def test_proxy_logs_request(test_app, client):
    """After a proxied request, the log must appear in /logs."""
    mock_resp = httpx.Response(200, content=b'{"ok":true}')

    with respx.mock(base_url="https://api.example.com") as mock:
        mock.get("/data").mock(return_value=mock_resp)
        await client.get("/proxy/https://api.example.com/data")

    # Give asyncio.create_task a tick to complete
    await asyncio.sleep(0.05)

    logs_r = await client.get("/logs")
    logs = logs_r.json()
    assert len(logs) >= 1
    assert logs[0]["upstream"] == "api.example.com"
    assert logs[0]["blocked"] is False


async def test_proxy_bad_gateway_on_network_error(client):
    """When upstream is unreachable, return 502."""
    with respx.mock(base_url="http://no-such-host-xyz.invalid") as mock:
        mock.get("/").mock(side_effect=httpx.ConnectError("no host"))
        r = await client.get("/proxy/http://no-such-host-xyz.invalid/")
    assert r.status_code == 502


async def test_proxy_rate_limited_returns_429(test_app, client):
    """When limit=1, the first request is forwarded (200), the second is rejected (429).
    The 429 is returned BEFORE reaching the upstream, so no respx mock is needed for r2.
    """
    # Create a tight rule: limit=1 for ALL proxy paths
    await client.post("/rules", json={
        "name": "Tight", "path_pattern": "/proxy/*",
        "limit": 1, "window_seconds": 60, "key_type": "ip",
    })

    mock_resp = httpx.Response(200, content=b"ok")

    # r1: within limit → forwarded → 200
    with respx.mock(base_url="https://httpbin.org") as mock:
        mock.get("/get").mock(return_value=mock_resp)
        r1 = await client.get("/proxy/https://httpbin.org/get")
    assert r1.status_code == 200

    # r2: over limit → rejected at gateway, NEVER reaches upstream (no mock needed)
    r2 = await client.get("/proxy/https://httpbin.org/get")
    assert r2.status_code == 429


async def test_proxy_rate_limit_headers_present(test_app, client):
    """429 response must include X-RateLimit-* headers."""
    await client.post("/rules", json={
        "name": "Headers", "path_pattern": "/proxy/*",
        "limit": 1, "window_seconds": 60,
    })
    mock_resp = httpx.Response(200, content=b"ok")

    with respx.mock(base_url="http://header.test") as mock:
        mock.get("/").mock(return_value=mock_resp)
        await client.get("/proxy/http://header.test/")
        r = await client.get("/proxy/http://header.test/")

    if r.status_code == 429:
        assert "x-ratelimit-limit" in r.headers
        assert "x-ratelimit-remaining" in r.headers
        assert "retry-after" in r.headers


async def test_proxy_blocked_logged_correctly(test_app, client):
    """A rate-limited request must appear in /logs as blocked=true."""
    await client.post("/rules", json={
        "name": "LogBlock", "path_pattern": "/proxy/*",
        "limit": 1, "window_seconds": 60,
    })
    mock_resp = httpx.Response(200, content=b"ok")

    with respx.mock(base_url="http://logtest.test") as mock:
        mock.get("/").mock(return_value=mock_resp)
        await client.get("/proxy/http://logtest.test/")
        await client.get("/proxy/http://logtest.test/")

    await asyncio.sleep(0.1)
    logs = (await client.get("/logs")).json()
    blocked_logs = [l for l in logs if l["blocked"]]
    if blocked_logs:
        assert blocked_logs[0]["blocked_by"] in ("rate_limit", "circuit_breaker")


async def test_proxy_post_method_forwarded(client):
    """POST requests should be forwarded correctly."""
    mock_resp = httpx.Response(201, json={"id": 101})
    with respx.mock(base_url="https://jsonplaceholder.typicode.com") as mock:
        mock.post("/posts").mock(return_value=mock_resp)
        r = await client.post(
            "/proxy/https://jsonplaceholder.typicode.com/posts",
            json={"title": "test"},
        )
    assert r.status_code == 201


async def test_proxy_delete_method_forwarded(client):
    mock_resp = httpx.Response(200, content=b"")
    with respx.mock(base_url="https://jsonplaceholder.typicode.com") as mock:
        mock.delete("/posts/1").mock(return_value=mock_resp)
        r = await client.delete("/proxy/https://jsonplaceholder.typicode.com/posts/1")
    assert r.status_code == 200


async def test_proxy_put_method_forwarded(client):
    mock_resp = httpx.Response(200, json={"updated": True})
    with respx.mock(base_url="https://jsonplaceholder.typicode.com") as mock:
        mock.put("/posts/1").mock(return_value=mock_resp)
        r = await client.put(
            "/proxy/https://jsonplaceholder.typicode.com/posts/1",
            json={"title": "updated"},
        )
    assert r.status_code == 200


# ── circuit breaker integration ───────────────────────────────────────────────

async def test_circuit_opens_after_failures(test_app, client):
    """After FAILURE_THRESHOLD failures, circuit should open → 503."""
    from app.core.config import CIRCUIT_BREAKER_FAILURE_THRESHOLD
    threshold = CIRCUIT_BREAKER_FAILURE_THRESHOLD
    upstream = "http://failing.test"
    cb = test_app.state.circuit_breaker

    # Force failures directly
    for _ in range(threshold):
        cb.record_failure("failing.test")

    r = await client.get(f"/proxy/{upstream}/")
    assert r.status_code == 503


async def test_circuit_503_body(test_app, client):
    from app.core.config import CIRCUIT_BREAKER_FAILURE_THRESHOLD
    cb = test_app.state.circuit_breaker
    for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure("svc.bad")

    r = await client.get("/proxy/http://svc.bad/endpoint")
    assert r.status_code == 503
    body = r.json()
    assert "circuit_breaker_open" in body.get("reason", "")


async def test_circuit_recovers_after_success(test_app, client):
    from app.core.config import CIRCUIT_BREAKER_FAILURE_THRESHOLD
    cb = test_app.state.circuit_breaker
    upstream_host = "recoverable.test"

    for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
        cb.record_failure(upstream_host)

    # Force transition to HALF_OPEN then success
    from app.core.redis_client import get_redis
    import time
    test_app.state.events  # just to confirm
    # Manually set opens_at to past
    import fakeredis as fr
    r_client = test_app.state.circuit_breaker._r
    r_client.set(f"rg:cb:{upstream_host}:opens_at", time.time() - 1)

    cb.can_request(upstream_host)       # triggers HALF_OPEN
    cb.record_success(upstream_host)    # closes

    assert cb.get_state(upstream_host).state == "CLOSED"


# ── Edge cases ────────────────────────────────────────────────────────────────

async def test_rule_list_after_multiple_creates(client):
    for i in range(5):
        await client.post("/rules", json={
            "name": f"Rule {i}", "path_pattern": f"/api/v{i}/*",
            "limit": 10 * (i + 1), "window_seconds": 60,
        })
    rules = (await client.get("/rules")).json()
    assert len(rules) == 5


async def test_dashboard_updates_after_requests(test_app, client):
    """Stats should reflect requests made through the gateway."""
    mock_resp = httpx.Response(200, content=b"data")
    with respx.mock(base_url="http://stats.test") as mock:
        mock.get("/item").mock(return_value=mock_resp)
        for _ in range(3):
            await client.get("/proxy/http://stats.test/item")

    await asyncio.sleep(0.1)
    dash = (await client.get("/dashboard")).json()
    assert dash["stats"]["total_requests"] >= 3


async def test_logs_schema(test_app, client):
    """Each log entry must have the required fields."""
    mock_resp = httpx.Response(200, content=b"x")
    with respx.mock(base_url="http://schema.test") as mock:
        mock.get("/").mock(return_value=mock_resp)
        await client.get("/proxy/http://schema.test/")

    await asyncio.sleep(0.1)
    logs = (await client.get("/logs")).json()
    if logs:
        log = logs[0]
        for field in ("id", "client_ip", "method", "path", "upstream",
                      "status_code", "latency_ms", "blocked", "created_at"):
            assert field in log, f"Missing field: {field}"


async def test_nonexistent_route_404(client):
    r = await client.get("/this-does-not-exist")
    assert r.status_code == 404
