# -*- coding: utf-8 -*-
"""
Rate Guardian - live terminal demo
Run:  python demo.py
Req:  uvicorn app.main:app --port 8002   (in a separate terminal)
      pip install rich
"""

import sys
import time

import httpx
import redis as _redis
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

BASE  = "http://localhost:8002"
DELAY = 0.4          # seconds between requests (keeps the live feed readable)

console = Console()
c       = httpx.Client(base_url=BASE, timeout=10)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flush_redis():
    r = _redis.from_url("redis://localhost:6379", decode_responses=True)
    for key in r.keys("rg:*"):
        r.delete(key)

def _clear_rules():
    for rule in c.get("/rules").json():
        c.delete(f"/rules/{rule['id']}")


# ── Renderables ───────────────────────────────────────────────────────────────

def _stats_panel(stats: dict) -> Panel:
    sat = stats["saturation_pct"]
    sat_color = "green" if sat < 40 else "dark_orange" if sat < 70 else "red"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="dim")
    grid.add_column()
    grid.add_row("Total",      f"[bold]{stats['total_requests']}[/]")
    grid.add_row("Allowed",    f"[green]{stats['allowed_requests']}[/]")
    grid.add_row("Blocked",    f"[red]{stats['blocked_requests']}[/]")
    grid.add_row("Latency",    f"[cyan]{stats['avg_latency_ms']:.1f} ms[/]")
    grid.add_row("Saturation", f"[{sat_color} bold]{sat:.1f}%[/]")

    return Panel(grid, title="[bold]Stats[/]", border_style="bright_blue", padding=(1, 2))


def _log_table(logs: list) -> Table:
    t = Table(box=box.SIMPLE_HEAD, expand=True, show_footer=False)
    t.add_column("Status",  width=20)
    t.add_column("Method",  width=7)
    t.add_column("Path")
    t.add_column("ms", width=7, justify="right")

    for log in logs[:14]:
        code = log["status_code"]
        if code == 200:
            status = Text(" 200 OK ", style="bold black on green")
        elif code == 429:
            status = Text(" 429 RATE LIMIT ", style="bold white on dark_orange")
        elif code == 503:
            status = Text(" 503 CIRCUIT OPEN ", style="bold white on red")
        elif code == 502:
            status = Text(" 502 BAD GATEWAY ", style="bold white on red3")
        else:
            status = Text(f" {code} ", style="dim")

        t.add_row(status, f"[bold]{log['method']}[/]",
                  log["path"][:52], f"[dim]{log['latency_ms']:.0f}[/]")
    return t


def _rules_table(rules: list) -> Table:
    t = Table(box=box.SIMPLE_HEAD, expand=True)
    t.add_column("", width=4)
    t.add_column("Name")
    t.add_column("Pattern")
    t.add_column("Limit", justify="right", width=12)
    for rl in rules:
        state = "[green]ON[/]" if rl["active"] else "[dim]OFF[/]"
        t.add_row(state, rl["name"], rl["path_pattern"],
                  f"{rl['limit']} / {rl['window_seconds']}s")
    return t


def _build_layout(logs, stats, rules, note: str) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top",   ratio=5),
        Layout(name="rules", ratio=2),
        Layout(name="note",  size=3),
    )
    layout["top"].split_row(
        Layout(_stats_panel(stats), name="stats", ratio=1),
        Layout(Panel(_log_table(logs), title="[bold]Request Log[/]",
                     border_style="bright_blue", padding=(0, 1)),
               name="log", ratio=3),
    )
    layout["rules"].update(
        Panel(_rules_table(rules), title="[bold]Rules[/]",
              border_style="bright_blue", padding=(0, 1))
    )
    layout["note"].update(
        Panel(Text(note, justify="center", style="italic"),
              border_style="dim")
    )
    return layout


# ── Request helper ────────────────────────────────────────────────────────────

def _get(live, url: str, note: str):
    c.get(url)
    time.sleep(DELAY)
    _refresh(live, note)


def _refresh(live, note: str):
    stats = c.get("/dashboard").json()["stats"]
    logs  = c.get("/logs?limit=14").json()
    rules = c.get("/rules").json()
    live.update(_build_layout(logs, stats, rules, note))


# ── Demo ──────────────────────────────────────────────────────────────────────

def main():
    _flush_redis()
    _clear_rules()

    console.rule("[bold bright_blue]  RATE GUARDIAN  —  Live Demo  [/]")
    console.print()

    UPSTREAM = "/proxy/https://jsonplaceholder.typicode.com/posts/1"

    with Live(console=console, refresh_per_second=8, screen=False) as live:

        # ── 1. No rules: gateway pass-through ─────────────────────────────
        note = "1 / 4   No rules configured — every request is forwarded."
        _refresh(live, note)
        time.sleep(1.2)
        for _ in range(5):
            _get(live, UPSTREAM, note)

        # ── 2. Create rate-limit rule ──────────────────────────────────────
        note = "2 / 4   Creating rule:  /proxy/*  →  max 3 req / 20 s per IP"
        _refresh(live, note)
        time.sleep(1.0)

        c.post("/rules", json={
            "name": "API rate limit", "path_pattern": "/proxy/*",
            "limit": 3, "window_seconds": 20, "key_type": "ip",
        })
        note = "2 / 4   Rule active. Watch requests start getting blocked (429)."
        _refresh(live, note)
        time.sleep(1.0)

        # ── 3. Hit the rate limit ──────────────────────────────────────────
        note = "3 / 4   Sending 7 requests.  Limit = 3 / 20 s."
        for _ in range(7):
            _get(live, UPSTREAM, note)
        time.sleep(1.2)

        # ── 4. Circuit breaker ─────────────────────────────────────────────
        rule_id = c.get("/rules").json()[0]["id"]
        c.patch(f"/rules/{rule_id}/toggle")    # disable rate limit

        note = "4 / 4   Rate-limit disabled. Hammering an unreachable upstream..."
        _refresh(live, note)
        time.sleep(1.0)

        DEAD = "/proxy/http://dead-host.invalid/api"
        tripped = False
        for _ in range(8):
            r = c.get(DEAD)
            time.sleep(DELAY)
            if r.status_code == 503 and not tripped:
                note = "4 / 4   Circuit OPEN — gateway short-circuits without touching upstream."
                tripped = True
            _refresh(live, note)

        time.sleep(2.5)
        _refresh(live, "Done.  Open http://localhost:8002 for the live dashboard.")
        time.sleep(2.0)

    console.print()
    console.rule("[bold bright_blue]  Demo complete  [/]")
    console.print(f"  [dim]Dashboard :[/] http://localhost:8002")
    console.print(f"  [dim]Swagger   :[/] http://localhost:8002/docs")
    console.print(f"  [dim]Tests     :[/] 202 passing  —  [italic]pytest tests/[/]")
    console.print()


if __name__ == "__main__":
    main()
