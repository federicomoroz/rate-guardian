# -*- coding: utf-8 -*-
"""
Rate Guardian - live demo
Run: python demo.py
Requires: uvicorn app.main:app --port 8002 running in another terminal
"""

import sys
import time
import httpx

# Force UTF-8 output on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://localhost:8002"
SEP  = "-" * 60


def hdr(title: str):
    print(f"\n{SEP}\n  {title}\n{SEP}")


def check(r: httpx.Response):
    if r.status_code < 400:
        icon = "OK "
    elif r.status_code == 429:
        icon = "429"
    elif r.status_code == 503:
        icon = "503"
    else:
        icon = str(r.status_code)
    print(f"  [{icon}]  {r.request.method} {str(r.url)[:60]}  ->  {r.status_code}")
    return r


import redis as _redis
_r = _redis.from_url("redis://localhost:6379", decode_responses=True)
for key in _r.keys("rg:*"):
    _r.delete(key)

c = httpx.Client(base_url=BASE, timeout=10)

# ── cleanup rules from previous runs ─────────────────────────────────────────
for rl in c.get("/rules").json():
    c.delete(f"/rules/{rl['id']}")

# ── 0. Dashboard before anything ─────────────────────────────────────────────
hdr("0 . Dashboard (empty state)")
r = c.get("/dashboard")
stats = r.json()["stats"]
print(f"  total={stats['total_requests']}  blocked={stats['blocked_requests']}  saturation={stats['saturation_pct']}%")

# ── 1. No rule -> requests pass freely ────────────────────────────────────────
hdr("1 . No rule -- all requests forwarded")
for i in range(3):
    check(c.get("/proxy/https://jsonplaceholder.typicode.com/posts/1"))

# ── 2. Create a tight rate-limiting rule ──────────────────────────────────────
hdr("2 . Create rule: /proxy/* -> max 3 req / 30 s per IP")
r = c.post("/rules", json={
    "name":           "Demo limit",
    "path_pattern":   "/proxy/*",
    "limit":          3,
    "window_seconds": 30,
    "key_type":       "ip",
})
rule = r.json()
print(f"  Rule created -> id={rule['id']}  pattern={rule['path_pattern']}  limit={rule['limit']}/30s")

# ── 3. Hit the rule ───────────────────────────────────────────────────────────
hdr("3 . Send 5 requests -- first 3 pass, last 2 are blocked (429)")
for i in range(5):
    check(c.get("/proxy/https://jsonplaceholder.typicode.com/posts/1"))

# ── 4. Dashboard after traffic ───────────────────────────────────────────────
hdr("4 . Dashboard after traffic")
time.sleep(0.2)
r = c.get("/dashboard")
stats = r.json()["stats"]
print(f"  total={stats['total_requests']}  allowed={stats['allowed_requests']}  "
      f"blocked={stats['blocked_requests']}  saturation={stats['saturation_pct']}%  "
      f"avg_latency={stats['avg_latency_ms']:.1f}ms")
top_ips = r.json()["top_ips"]
if top_ips:
    print(f"  top IP: {top_ips[0]['ip']}  ({top_ips[0]['count']} requests)")

# ── 5. Recent logs ────────────────────────────────────────────────────────────
hdr("5 . Recent request logs")
logs = c.get("/logs?limit=8").json()
for log in logs[:8]:
    status = "BLOCKED" if log["blocked"] else "OK"
    print(f"  [{status:7}]  {log['method']} {log['path'][:40]}  "
          f"status={log['status_code']}  latency={log['latency_ms']:.1f}ms")

# ── 6. Disable rate limit rule, then show circuit breaker ─────────────────────
hdr("6 . Disable rate-limit rule, hammer a dead upstream (threshold=5 failures)")
c.patch(f"/rules/{rule['id']}/toggle")
print("  Rate limit rule disabled.")
print("\n  Sending requests to an unreachable host:")
for i in range(7):
    r = check(c.get("/proxy/http://dead-host.invalid/api"))
    if r.status_code == 503:
        print("       ^ circuit OPEN -- gateway blocks without touching the upstream")
        break

# ── 7. Show rule list ─────────────────────────────────────────────────────────
hdr("7 . Current rules")
for rl in c.get("/rules").json():
    state = "ON " if rl["active"] else "OFF"
    print(f"  [{state}]  '{rl['name']}'  pattern={rl['path_pattern']}  limit={rl['limit']}/30s")

# ── 8. Requests pass freely again ─────────────────────────────────────────────
hdr("8 . No active rate-limit rule -- requests forwarded again")
for i in range(2):
    check(c.get("/proxy/https://jsonplaceholder.typicode.com/posts/1"))

print(f"\n{SEP}")
print("  Dashboard: http://localhost:8002")
print("  API docs:  http://localhost:8002/docs")
print(SEP)
