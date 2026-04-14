"""
Unit tests — LogListener.
Verifies that LogListener subscribes to events and delegates correctly to LogService.
Uses mock to avoid real asyncio.create_task dependencies.
"""

import pytest
from unittest.mock import patch, call

from app.core.event_manager import EventManager
from app.core.events import RequestBlocked, RequestForwarded
from app.models.services.log_listener import LogListener


def _blocked(**kw) -> RequestBlocked:
    d = dict(client_ip="1.2.3.4", method="GET", path="/p",
             upstream="up", status_code=429, latency_ms=5.0, reason="rate_limit")
    return RequestBlocked(**{**d, **kw})


def _forwarded(**kw) -> RequestForwarded:
    d = dict(client_ip="1.2.3.4", method="GET", path="/p",
             upstream="up", status_code=200, latency_ms=50.0)
    return RequestForwarded(**{**d, **kw})


# ── subscription wiring ───────────────────────────────────────────────────────

def test_listener_registers_on_construction():
    em = EventManager()
    LogListener(db_factory=lambda: None, events=em)
    assert len(em._handlers[RequestBlocked]) == 1
    assert len(em._handlers[RequestForwarded]) == 1


def test_multiple_listeners_each_register():
    em = EventManager()
    LogListener(db_factory=lambda: None, events=em)
    LogListener(db_factory=lambda: None, events=em)
    assert len(em._handlers[RequestBlocked]) == 2


# ── on_blocked ────────────────────────────────────────────────────────────────

def test_blocked_event_calls_record_async():
    em = EventManager()
    db_factory = object()
    with patch("app.models.services.log_listener.LogService.record_async") as mock_record:
        LogListener(db_factory=db_factory, events=em)
        ev = _blocked(
            client_ip="9.9.9.9", method="POST", path="/api",
            upstream="svc", status_code=429, latency_ms=2.5, reason="rate_limit",
        )
        em.emit(ev)
        mock_record.assert_called_once_with(
            db_factory,
            client_ip="9.9.9.9",
            method="POST",
            path="/api",
            upstream="svc",
            status_code=429,
            latency_ms=2.5,
            blocked=True,
            blocked_by="rate_limit",
        )


def test_blocked_circuit_breaker_reason():
    em = EventManager()
    with patch("app.models.services.log_listener.LogService.record_async") as mock_record:
        LogListener(db_factory=None, events=em)
        em.emit(_blocked(reason="circuit_breaker", status_code=503))
        _, kwargs = mock_record.call_args
        assert kwargs["blocked_by"] == "circuit_breaker"
        assert kwargs["status_code"] == 503
        assert kwargs["blocked"] is True


def test_blocked_upstream_error_reason():
    em = EventManager()
    with patch("app.models.services.log_listener.LogService.record_async") as mock_record:
        LogListener(db_factory=None, events=em)
        em.emit(_blocked(reason="upstream_error", status_code=502))
        _, kwargs = mock_record.call_args
        assert kwargs["blocked_by"] == "upstream_error"


# ── on_forwarded ──────────────────────────────────────────────────────────────

def test_forwarded_event_calls_record_async():
    em = EventManager()
    db_factory = object()
    with patch("app.models.services.log_listener.LogService.record_async") as mock_record:
        LogListener(db_factory=db_factory, events=em)
        ev = _forwarded(
            client_ip="5.5.5.5", method="DELETE", path="/res/1",
            upstream="backend", status_code=204, latency_ms=99.9,
        )
        em.emit(ev)
        mock_record.assert_called_once_with(
            db_factory,
            client_ip="5.5.5.5",
            method="DELETE",
            path="/res/1",
            upstream="backend",
            status_code=204,
            latency_ms=99.9,
        )


def test_forwarded_not_blocked():
    """Forwarded events must NOT pass blocked=True."""
    em = EventManager()
    with patch("app.models.services.log_listener.LogService.record_async") as mock_record:
        LogListener(db_factory=None, events=em)
        em.emit(_forwarded())
        _, kwargs = mock_record.call_args
        # blocked_by should not be set
        assert "blocked_by" not in kwargs or kwargs.get("blocked_by") is None
        # blocked should not be True
        assert kwargs.get("blocked", False) is not True


# ── isolation ─────────────────────────────────────────────────────────────────

def test_blocked_does_not_trigger_forwarded_handler():
    em = EventManager()
    forwarded_calls = []
    with patch("app.models.services.log_listener.LogService.record_async"):
        LogListener(db_factory=None, events=em)
        em.subscribe(RequestForwarded, lambda e: forwarded_calls.append(e))
        em.emit(_blocked())
    assert forwarded_calls == []


def test_forwarded_does_not_trigger_blocked_handler():
    em = EventManager()
    blocked_calls = []
    with patch("app.models.services.log_listener.LogService.record_async"):
        LogListener(db_factory=None, events=em)
        em.subscribe(RequestBlocked, lambda e: blocked_calls.append(e))
        em.emit(_forwarded())
    assert blocked_calls == []


def test_multiple_events_call_record_multiple_times():
    em = EventManager()
    with patch("app.models.services.log_listener.LogService.record_async") as mock_record:
        LogListener(db_factory=None, events=em)
        em.emit(_blocked())
        em.emit(_forwarded())
        em.emit(_blocked(reason="circuit_breaker"))
        assert mock_record.call_count == 3
