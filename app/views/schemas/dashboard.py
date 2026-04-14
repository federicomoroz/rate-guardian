from pydantic import BaseModel


class StatsResponse(BaseModel):
    total_requests:   int
    blocked_requests: int
    allowed_requests: int
    avg_latency_ms:   float
    window_minutes:   int
    saturation_pct:   float  # blocked / total * 100


class TopIpEntry(BaseModel):
    ip:    str
    count: int


class CircuitStateResponse(BaseModel):
    upstream: str
    state:    str
    failures: int


class DashboardResponse(BaseModel):
    stats:           StatsResponse
    top_ips:         list[TopIpEntry]
    circuit_breakers: list[CircuitStateResponse]
