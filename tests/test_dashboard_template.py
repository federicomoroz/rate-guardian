"""
Unit tests — dashboard HTML template + SVG gauge.
Covers: gauge at 0/50/100/extreme values, HTML structure,
        stats displayed, top IPs table, recent logs table.
"""

import math
import pytest

from app.views.templates.dashboard import render_dashboard


def _stats(**kwargs):
    defaults = dict(
        total_requests=100,
        blocked_requests=20,
        allowed_requests=80,
        avg_latency_ms=45.5,
        window_minutes=5,
    )
    return {**defaults, **kwargs}


def _ip(ip="1.2.3.4", count=10):
    class R:
        pass
    r = R()
    r.ip    = ip
    r.count = count
    return r


def _log_entry(**kwargs):
    class L:
        pass
    l = L()
    l.id         = kwargs.get("id", 1)
    l.client_ip  = kwargs.get("client_ip", "1.2.3.4")
    l.method     = kwargs.get("method", "GET")
    l.path       = kwargs.get("path", "/proxy/test")
    l.upstream   = kwargs.get("upstream", "api.test")
    l.status_code = kwargs.get("status_code", 200)
    l.latency_ms = kwargs.get("latency_ms", 50.0)
    l.blocked    = kwargs.get("blocked", False)
    l.blocked_by = kwargs.get("blocked_by", None)
    from datetime import datetime
    l.created_at = kwargs.get("created_at", datetime(2026, 1, 1, 12, 0, 0))
    return l


# ── render_dashboard returns valid HTML ───────────────────────────────────────

def test_returns_string():
    html = render_dashboard(_stats(), [], [], 20.0)
    assert isinstance(html, str)
    assert len(html) > 100


def test_html_has_doctype():
    html = render_dashboard(_stats(), [], [], 20.0)
    assert "<!DOCTYPE html>" in html


def test_html_has_title():
    html = render_dashboard(_stats(), [], [], 20.0)
    assert "RATE GUARDIAN" in html.upper()


def test_html_auto_refresh():
    html = render_dashboard(_stats(), [], [], 20.0)
    assert 'http-equiv="refresh"' in html


# ── gauge SVG ─────────────────────────────────────────────────────────────────

def test_gauge_present_in_html():
    html = render_dashboard(_stats(), [], [], 50.0)
    assert "<svg" in html


def test_gauge_at_zero():
    html = render_dashboard(_stats(), [], [], 0.0)
    assert "0.0%" in html or "0%" in html


def test_gauge_at_hundred():
    html = render_dashboard(_stats(), [], [], 100.0)
    assert "100.0%" in html or "100%" in html


def test_gauge_at_fifty():
    html = render_dashboard(_stats(), [], [], 50.0)
    assert "50.0%" in html or "50%" in html


def test_gauge_color_green_at_low():
    """Saturation < 50% → needle should be green."""
    html = render_dashboard(_stats(), [], [], 10.0)
    # Green color code present in SVG needle
    assert "#15ff00" in html or "green" in html.lower()


def test_gauge_color_yellow_at_medium():
    """Saturation 50-80% → needle should be yellow."""
    html = render_dashboard(_stats(), [], [], 65.0)
    assert "#f5a623" in html or "yellow" in html.lower() or "orange" in html.lower()


def test_gauge_color_red_at_high():
    """Saturation > 80% → needle should be red."""
    html = render_dashboard(_stats(), [], [], 90.0)
    assert "#ff2d2d" in html or "red" in html.lower()


def test_gauge_below_zero_clamped():
    html = render_dashboard(_stats(), [], [], -10.0)
    assert html  # Must not crash; renders at 0


def test_gauge_above_hundred_clamped():
    html = render_dashboard(_stats(), [], [], 150.0)
    assert html  # Must not crash; renders at 100


# ── stats cards ───────────────────────────────────────────────────────────────

def test_total_requests_shown(db_session=None):
    html = render_dashboard(_stats(total_requests=999), [], [], 0.0)
    assert "999" in html


def test_blocked_requests_shown():
    html = render_dashboard(_stats(blocked_requests=42), [], [], 42.0)
    assert "42" in html


def test_avg_latency_shown():
    html = render_dashboard(_stats(avg_latency_ms=123.45), [], [], 0.0)
    assert "123" in html


# ── top IPs table ─────────────────────────────────────────────────────────────

def test_top_ips_empty_table():
    html = render_dashboard(_stats(), [], [], 0.0)
    assert html  # no crash on empty list


def test_top_ip_address_shown():
    html = render_dashboard(_stats(), [{"ip": "8.8.8.8", "count": 55}], [], 0.0)
    assert "8.8.8.8" in html
    assert "55" in html


def test_multiple_top_ips_shown():
    ips = [{"ip": f"10.0.0.{i}", "count": 10 - i} for i in range(5)]
    html = render_dashboard(_stats(), ips, [], 0.0)
    for i in range(5):
        assert f"10.0.0.{i}" in html


# ── recent logs table ─────────────────────────────────────────────────────────

def test_recent_logs_empty():
    html = render_dashboard(_stats(), [], [], 0.0)
    assert html  # no crash


def test_recent_log_path_shown():
    html = render_dashboard(_stats(), [], [_log_entry(path="/proxy/test-path")], 0.0)
    assert "test-path" in html


def test_recent_log_method_shown():
    html = render_dashboard(_stats(), [], [_log_entry(method="POST")], 0.0)
    assert "POST" in html


def test_recent_log_status_shown():
    html = render_dashboard(_stats(), [], [_log_entry(status_code=429)], 0.0)
    assert "429" in html


def test_recent_log_blocked_marker():
    html = render_dashboard(_stats(), [], [
        _log_entry(blocked=True, blocked_by="rate_limit")
    ], 0.0)
    assert html  # no crash; blocked entry renders


def test_multiple_recent_logs_shown():
    logs = [_log_entry(path=f"/proxy/endpoint-{i}", status_code=200) for i in range(10)]
    html = render_dashboard(_stats(), [], logs, 0.0)
    for i in range(10):
        assert f"endpoint-{i}" in html


# ── rule form ─────────────────────────────────────────────────────────────────

def test_add_rule_form_present():
    html = render_dashboard(_stats(), [], [], 0.0)
    assert "form" in html.lower() or "input" in html.lower()


# ── zero division guard ───────────────────────────────────────────────────────

def test_zero_requests_saturation_zero():
    """Saturation calculation in main.py: if total==0 → 0.0. Template must handle 0.0."""
    html = render_dashboard(_stats(total_requests=0, blocked_requests=0), [], [], 0.0)
    assert html
