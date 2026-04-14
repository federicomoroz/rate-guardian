"""
Unit tests — LogRepository.
Covers: create, get_recent, blocked_only filter, stats aggregation,
        top_ips, delete_older_than, edge cases (empty DB, 0 requests).
"""

from datetime import datetime, timedelta
import pytest

from app.models.request_log import RequestLog
from app.models.repositories.log_repository import LogRepository


def _log(db_session, **kwargs):
    defaults = dict(
        client_ip="1.2.3.4", method="GET", path="/test",
        upstream="api.test", status_code=200, latency_ms=50.0,
        blocked=False, blocked_by=None,
    )
    log = RequestLog(**{**defaults, **kwargs})
    LogRepository.create(db_session, log)
    return log


# ── create / get_recent ───────────────────────────────────────────────────────

def test_create_and_retrieve(db_session):
    _log(db_session, client_ip="10.0.0.1")
    logs = LogRepository.get_recent(db_session, limit=10)
    assert len(logs) == 1
    assert logs[0].client_ip == "10.0.0.1"


def test_get_recent_returns_newest_first(db_session):
    _log(db_session, path="/first")
    _log(db_session, path="/second")
    logs = LogRepository.get_recent(db_session, limit=10)
    assert logs[0].path == "/second"
    assert logs[1].path == "/first"


def test_get_recent_limit_respected(db_session):
    for i in range(20):
        _log(db_session, path=f"/p{i}")
    logs = LogRepository.get_recent(db_session, limit=5)
    assert len(logs) == 5


def test_get_recent_empty_db(db_session):
    logs = LogRepository.get_recent(db_session, limit=50)
    assert logs == []


# ── blocked_only filter ───────────────────────────────────────────────────────

def test_blocked_only_false_returns_all(db_session):
    _log(db_session, blocked=False)
    _log(db_session, blocked=True, blocked_by="rate_limit")
    logs = LogRepository.get_recent(db_session, blocked_only=False)
    assert len(logs) == 2


def test_blocked_only_true_filters(db_session):
    _log(db_session, blocked=False)
    _log(db_session, blocked=True, blocked_by="rate_limit")
    _log(db_session, blocked=True, blocked_by="circuit_breaker")
    logs = LogRepository.get_recent(db_session, blocked_only=True)
    assert len(logs) == 2
    assert all(l.blocked for l in logs)


def test_blocked_only_no_blocked_returns_empty(db_session):
    for _ in range(5):
        _log(db_session, blocked=False)
    logs = LogRepository.get_recent(db_session, blocked_only=True)
    assert logs == []


# ── stats ─────────────────────────────────────────────────────────────────────

def test_stats_empty_db(db_session):
    s = LogRepository.stats(db_session, since_minutes=5)
    assert s["total_requests"] == 0
    assert s["blocked_requests"] == 0
    assert s["allowed_requests"] == 0
    assert s["avg_latency_ms"] == 0.0


def test_stats_counts(db_session):
    _log(db_session, blocked=False, latency_ms=100.0)
    _log(db_session, blocked=False, latency_ms=200.0)
    _log(db_session, blocked=True,  latency_ms=10.0, blocked_by="rate_limit")
    s = LogRepository.stats(db_session, since_minutes=5)
    assert s["total_requests"] == 3
    assert s["blocked_requests"] == 1
    assert s["allowed_requests"] == 2
    assert abs(s["avg_latency_ms"] - 103.33) < 1.0


def test_stats_window_excludes_old(db_session):
    # Old log (8 minutes ago) — outside the 5-minute window
    old_log = RequestLog(
        client_ip="1.2.3.4", method="GET", path="/old",
        upstream="x", status_code=200, latency_ms=100.0, blocked=False,
    )
    old_log.created_at = datetime.utcnow() - timedelta(minutes=8)
    LogRepository.create(db_session, old_log)

    # Recent log
    _log(db_session, latency_ms=200.0)

    s = LogRepository.stats(db_session, since_minutes=5)
    assert s["total_requests"] == 1
    assert s["avg_latency_ms"] == 200.0


def test_stats_all_blocked(db_session):
    for _ in range(5):
        _log(db_session, blocked=True, blocked_by="rate_limit")
    s = LogRepository.stats(db_session, since_minutes=5)
    assert s["total_requests"] == 5
    assert s["blocked_requests"] == 5
    assert s["allowed_requests"] == 0


def test_stats_window_minutes_field(db_session):
    s = LogRepository.stats(db_session, since_minutes=15)
    assert s["window_minutes"] == 15


# ── top_ips ───────────────────────────────────────────────────────────────────

def test_top_ips_empty(db_session):
    result = LogRepository.top_ips(db_session, limit=5, since_minutes=60)
    assert result == []


def test_top_ips_counts_correctly(db_session):
    for _ in range(5):
        _log(db_session, client_ip="10.0.0.1")
    for _ in range(3):
        _log(db_session, client_ip="10.0.0.2")
    _log(db_session, client_ip="10.0.0.3")

    top = LogRepository.top_ips(db_session, limit=3, since_minutes=60)
    assert top[0]["ip"] == "10.0.0.1"
    assert top[0]["count"] == 5
    assert top[1]["ip"] == "10.0.0.2"
    assert top[1]["count"] == 3


def test_top_ips_limit_respected(db_session):
    for i in range(10):
        _log(db_session, client_ip=f"192.168.0.{i}")
    top = LogRepository.top_ips(db_session, limit=3, since_minutes=60)
    assert len(top) <= 3


def test_top_ips_excludes_old(db_session):
    old_log = RequestLog(
        client_ip="old.ip", method="GET", path="/old",
        upstream="x", status_code=200, latency_ms=1.0, blocked=False,
    )
    old_log.created_at = datetime.utcnow() - timedelta(minutes=90)
    LogRepository.create(db_session, old_log)
    _log(db_session, client_ip="new.ip")

    top = LogRepository.top_ips(db_session, limit=10, since_minutes=60)
    ips = [t["ip"] for t in top]
    assert "new.ip" in ips
    assert "old.ip" not in ips


# ── delete_older_than ─────────────────────────────────────────────────────────

def test_delete_older_than_removes_old(db_session):
    old_log = RequestLog(
        client_ip="1.2.3.4", method="GET", path="/old",
        upstream="x", status_code=200, latency_ms=1.0, blocked=False,
    )
    old_log.created_at = datetime.utcnow() - timedelta(days=10)
    LogRepository.create(db_session, old_log)
    _log(db_session)  # recent

    deleted = LogRepository.delete_older_than(db_session, days=7)
    assert deleted == 1
    remaining = LogRepository.get_recent(db_session, limit=50)
    assert len(remaining) == 1


def test_delete_older_than_zero_deleted(db_session):
    _log(db_session)  # recent log
    deleted = LogRepository.delete_older_than(db_session, days=7)
    assert deleted == 0


def test_delete_older_than_empty_db(db_session):
    deleted = LogRepository.delete_older_than(db_session, days=7)
    assert deleted == 0


def test_delete_older_than_all(db_session):
    for _ in range(5):
        old = RequestLog(
            client_ip="1.1.1.1", method="GET", path="/x",
            upstream="y", status_code=200, latency_ms=1.0, blocked=False,
        )
        old.created_at = datetime.utcnow() - timedelta(days=30)
        LogRepository.create(db_session, old)

    deleted = LogRepository.delete_older_than(db_session, days=1)
    assert deleted == 5
    assert LogRepository.get_recent(db_session) == []
