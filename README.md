# Rate Guardian

An API gateway built with FastAPI that provides sliding-window rate limiting, a Redis-backed circuit breaker, transparent HTTP proxying, and a real-time terminal-aesthetic dashboard.

---

## What It Does

Rate Guardian sits in front of your upstream APIs and enforces rules you define:

- **Rate limiting** — sliding-window algorithm per IP or globally, backed by Redis sorted sets
- **Circuit breaker** — automatically opens when an upstream fails repeatedly, half-opens after a recovery period, and closes once the probe request succeeds
- **Transparent proxy** — forwards any HTTP method to any upstream URL, stripping hop-by-hop headers
- **Dashboard** — real-time HTML dashboard with an SVG saturation gauge, top IPs table, and recent request log; auto-refreshes every 5 seconds
- **JSON API** — all stats, logs, and rules also exposed as JSON endpoints
- **Event-driven logging** — every proxied or blocked request is logged to SQLite asynchronously via an event bus; old logs purged automatically

---

## Architecture

The project follows **MVC + Repository + Composition Root** with strict SOLID principles. Components communicate via a synchronous pub/sub `EventManager` instead of direct imports, keeping every layer decoupled.

```
app/
├── core/
│   ├── config.py          # Environment/config constants
│   ├── database.py        # SQLAlchemy engine + session factory
│   ├── event_manager.py   # Pub/sub event bus (subscribe/emit)
│   ├── events.py          # Domain events: RequestBlocked, RequestForwarded
│   └── redis_client.py    # Shared Redis singleton
│
├── controllers/
│   ├── gateway.py         # Thin entry point — builds PipelineContext, runs pipeline
│   ├── pipeline.py        # Composable pipeline: RateLimitStep → CircuitBreakerStep → ProxyStep
│   ├── dashboard.py       # /dashboard JSON + /logs routes
│   └── rules.py           # /rules CRUD routes
│
├── models/
│   ├── rule.py            # Rule SQLAlchemy model
│   ├── request_log.py     # RequestLog SQLAlchemy model
│   ├── repositories/
│   │   ├── rule_repository.py   # Rule persistence + Redis cache
│   │   └── log_repository.py   # Log persistence + stats aggregation
│   └── services/
│       ├── interfaces.py        # Abstract base classes (RateLimiterBase, etc.)
│       ├── rate_limiter.py      # Sliding-window rate limiter (Redis ZSET)
│       ├── circuit_breaker.py   # State machine: CLOSED → OPEN → HALF_OPEN
│       ├── proxy.py             # httpx-based HTTP forwarder
│       ├── log_listener.py      # Subscribes to events, delegates to LogService
│       └── log_service.py       # Async log writer (asyncio.create_task)
│
├── views/
│   ├── schemas/           # Pydantic request/response schemas
│   └── templates/
│       └── dashboard.py   # Pure-Python HTML + SVG gauge renderer
│
└── main.py                # Composition root — wires everything, starts scheduler
```

### Request Flow

```
Client request
  └─► GET /proxy/{upstream_url}
        └─► GatewayController.handle()
              └─► GatewayPipeline.run()
                    ├─► RateLimitStep    — check Redis ZSET; 429 if over limit
                    ├─► CircuitBreakerStep — check circuit state; 503 if OPEN
                    └─► ProxyStep       — forward via httpx; update circuit state
                          │
                          ▼ emits RequestForwarded / RequestBlocked
                          │
                    EventManager.emit()
                          │
                    LogListener.on_event()
                          │
                    LogService.record_async()  ← asyncio.create_task (fire-and-forget)
                          │
                    SQLite (request_logs table)
```

### SOLID Application

| Principle | How |
|-----------|-----|
| **S** — Single Responsibility | Each pipeline step does exactly one thing; `LogListener` only bridges events to `LogService` |
| **O** — Open/Closed | New pipeline steps added without modifying `GatewayPipeline`; new event types without modifying `EventManager` |
| **L** — Liskov Substitution | All steps honour the `PipelineStep` protocol; `FakeRedis` substitutes for real Redis in tests |
| **I** — Interface Segregation | `RateLimiterBase`, `CircuitBreakerBase`, `ProxyBase` each expose only the methods callers need |
| **D** — Dependency Inversion | `GatewayController` depends on abstractions; concrete services injected at composition root |

---

## Live Demo

Run the animated terminal demo to see rate limiting and the circuit breaker in action — no manual curl commands needed.

**Terminal 1 — start the gateway:**
```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8002
```

**Terminal 2 — run the demo:**
```bash
python demo.py
```

The demo runs four scenes automatically:

| Scene | What happens |
|-------|-------------|
| 1 — Pass-through | No rules configured; every request returns `200 OK` |
| 2 — Rule created | A rule is added: `/proxy/*` → max 3 requests / 20 s per IP |
| 3 — Rate limited | 7 requests sent; first 3 pass, the rest return `429 Rate Limit` |
| 4 — Circuit breaker | Rate limit disabled; gateway hammers an unreachable host until the circuit opens (`503 Circuit Open`) |

The layout refreshes in real time: a stats panel (total / allowed / blocked / latency / saturation), a live request log with colour-coded status codes, the active rules table, and a scene description bar at the bottom.

---

## Running Locally

**Prerequisites:** Python 3.11+, Redis running on `localhost:6379`

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8002 --reload
```

Open `http://localhost:8002` for the dashboard.

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | HTML dashboard with gauge, stats, and logs |
| `GET` | `/dashboard` | JSON stats (total/blocked/allowed, avg latency, top IPs) |
| `GET` | `/logs` | Recent request logs (filterable, paginated) |
| `GET` | `/rules` | List all rate-limiting rules |
| `POST` | `/rules` | Create a rule |
| `PATCH` | `/rules/{id}/toggle` | Enable/disable a rule |
| `DELETE` | `/rules/{id}` | Delete a rule |
| `GET/POST/…` | `/proxy/{upstream_url}` | Forward to upstream URL |

### Creating a rule

```bash
curl -X POST http://localhost:8002/rules \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Limit API",
    "path_pattern": "/proxy/https://api.example.com/*",
    "limit": 100,
    "window_seconds": 60,
    "key_type": "ip"
  }'
```

`key_type` is `"ip"` (per-client) or `"global"` (shared across all clients).

---

## Tests

```bash
pip install pytest pytest-asyncio httpx fakeredis respx
pytest tests/ -v
```

202 tests across 8 modules covering:
- Event bus pub/sub and handler isolation
- Sliding-window rate limiter (boundary cases, window expiry, quota preservation on reject)
- Circuit breaker state machine (CLOSED → OPEN → HALF_OPEN → CLOSED, multi-upstream isolation)
- Log and rule repositories (CRUD, stats aggregation, Redis caching)
- Pipeline steps in isolation and end-to-end (mocked dependencies)
- Dashboard HTML template (SVG gauge colors/clamping, stats cards, tables)
- All API routes via ASGI test client (rate limiting, circuit breaker, proxy methods, validation)

---

## Configuration

Set via environment variables or `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection string |
| `DATABASE_URL` | `sqlite:///./rate_guardian.db` | SQLAlchemy DB URL |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Failures before circuit opens |
| `CIRCUIT_BREAKER_RECOVERY_SECONDS` | `30` | Seconds before half-open probe |
| `LOG_RETENTION_DAYS` | `7` | Days to keep request logs |
