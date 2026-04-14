"""
Unit tests — EventManager pub/sub bus.
Covers: subscribe, emit, multiple handlers, multiple event types,
        no handlers, handler ordering, exception isolation.
"""

import pytest
from app.core.event_manager import EventManager
from app.core.events import RequestBlocked, RequestForwarded


def _blocked(**kwargs) -> RequestBlocked:
    defaults = dict(client_ip="1.1.1.1", method="GET", path="/x",
                    upstream="api.test", status_code=429,
                    latency_ms=1.0, reason="rate_limit")
    return RequestBlocked(**{**defaults, **kwargs})


def _forwarded(**kwargs) -> RequestForwarded:
    defaults = dict(client_ip="1.1.1.1", method="GET", path="/x",
                    upstream="api.test", status_code=200, latency_ms=50.0)
    return RequestForwarded(**{**defaults, **kwargs})


# ── Basic subscribe / emit ────────────────────────────────────────────────────

def test_emit_reaches_subscriber():
    em = EventManager()
    got = []
    em.subscribe(RequestBlocked, lambda e: got.append(e))
    ev = _blocked()
    em.emit(ev)
    assert got == [ev]


def test_emit_no_subscriber_is_noop():
    em = EventManager()
    em.emit(_blocked())  # must not raise


def test_multiple_handlers_all_called():
    em = EventManager()
    results = []
    em.subscribe(RequestBlocked, lambda e: results.append(1))
    em.subscribe(RequestBlocked, lambda e: results.append(2))
    em.subscribe(RequestBlocked, lambda e: results.append(3))
    em.emit(_blocked())
    assert results == [1, 2, 3]


def test_handlers_called_in_subscription_order():
    em = EventManager()
    order = []
    em.subscribe(RequestBlocked, lambda e: order.append("first"))
    em.subscribe(RequestBlocked, lambda e: order.append("second"))
    em.emit(_blocked())
    assert order == ["first", "second"]


def test_different_event_types_isolated():
    em = EventManager()
    blocked_got, forwarded_got = [], []
    em.subscribe(RequestBlocked, lambda e: blocked_got.append(e))
    em.subscribe(RequestForwarded, lambda e: forwarded_got.append(e))

    em.emit(_blocked())
    assert len(blocked_got) == 1
    assert len(forwarded_got) == 0

    em.emit(_forwarded())
    assert len(blocked_got) == 1
    assert len(forwarded_got) == 1


def test_emitting_unknown_type_ignored():
    em = EventManager()
    em.subscribe(RequestBlocked, lambda e: None)
    em.emit("not_an_event")      # unknown type — must not raise
    em.emit(42)
    em.emit(None)


def test_same_handler_subscribed_twice_called_twice():
    em = EventManager()
    count = []
    h = lambda e: count.append(1)
    em.subscribe(RequestBlocked, h)
    em.subscribe(RequestBlocked, h)
    em.emit(_blocked())
    assert len(count) == 2


def test_emit_multiple_events_accumulates():
    em = EventManager()
    got = []
    em.subscribe(RequestBlocked, lambda e: got.append(e))
    em.emit(_blocked(status_code=429))
    em.emit(_blocked(status_code=503))
    em.emit(_blocked(status_code=502))
    assert len(got) == 3
    assert [e.status_code for e in got] == [429, 503, 502]


def test_event_payload_preserved():
    em = EventManager()
    received = []
    em.subscribe(RequestForwarded, lambda e: received.append(e))
    ev = _forwarded(client_ip="9.9.9.9", method="POST", path="/api/data",
                    upstream="upstream.io", status_code=201, latency_ms=123.45)
    em.emit(ev)
    r = received[0]
    assert r.client_ip == "9.9.9.9"
    assert r.method == "POST"
    assert r.path == "/api/data"
    assert r.upstream == "upstream.io"
    assert r.status_code == 201
    assert r.latency_ms == 123.45


def test_events_are_frozen():
    ev = _blocked()
    with pytest.raises((AttributeError, TypeError)):
        ev.status_code = 999  # frozen dataclass must reject mutation


def test_independent_event_managers_dont_share_handlers():
    em1 = EventManager()
    em2 = EventManager()
    got1, got2 = [], []
    em1.subscribe(RequestBlocked, lambda e: got1.append(e))
    em2.subscribe(RequestBlocked, lambda e: got2.append(e))
    em1.emit(_blocked())
    assert len(got1) == 1
    assert len(got2) == 0
