from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.repositories.log_repository import LogRepository
from app.views.schemas.dashboard import (
    CircuitStateResponse,
    DashboardResponse,
    StatsResponse,
    TopIpEntry,
)
from app.views.schemas.request_log import LogResponse

router = APIRouter(tags=["Dashboard"])


@router.get("/dashboard", response_model=DashboardResponse, summary="Live gateway stats")
def dashboard_json(
    window: int = Query(5, ge=1, le=60, description="Minutes window for stats"),
    db: Session = Depends(get_db),
):
    stats   = LogRepository.stats(db, since_minutes=window)
    top_ips = LogRepository.top_ips(db, limit=10, since_minutes=window * 12)

    saturation = (
        round(stats["blocked_requests"] / stats["total_requests"] * 100, 1)
        if stats["total_requests"] > 0 else 0.0
    )

    return DashboardResponse(
        stats=StatsResponse(**stats, saturation_pct=saturation),
        top_ips=[TopIpEntry(**ip) for ip in top_ips],
        circuit_breakers=[],
    )


@router.get("/logs", response_model=list[LogResponse], summary="Recent request logs")
def get_logs(
    blocked: bool | None = Query(None, description="Filter by blocked status"),
    limit:   int         = Query(50, ge=1, le=500),
    db:      Session     = Depends(get_db),
):
    return LogRepository.get_recent(db, limit=limit, blocked_only=blocked or False)
