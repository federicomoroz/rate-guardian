from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.request_log import RequestLog


class LogRepository:
    """
    Write-heavy repository for request logs.

    S — responsible only for log persistence and aggregation queries.
    """

    @staticmethod
    def create(db: Session, log: RequestLog) -> None:
        db.add(log)
        db.commit()

    @staticmethod
    def get_recent(db: Session, limit: int = 50, blocked_only: bool = False) -> list[RequestLog]:
        q = db.query(RequestLog)
        if blocked_only:
            q = q.filter(RequestLog.blocked == True)
        return q.order_by(RequestLog.created_at.desc()).limit(limit).all()

    @staticmethod
    def delete_older_than(db: Session, days: int) -> int:
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = db.query(RequestLog).filter(RequestLog.created_at < cutoff).delete()
        db.commit()
        return deleted

    @staticmethod
    def stats(db: Session, since_minutes: int = 5) -> dict:
        since = datetime.utcnow() - timedelta(minutes=since_minutes)
        q = db.query(RequestLog).filter(RequestLog.created_at >= since)

        total    = q.count()
        blocked  = q.filter(RequestLog.blocked == True).count()
        avg_lat  = db.query(func.avg(RequestLog.latency_ms)).filter(
            RequestLog.created_at >= since
        ).scalar()

        return {
            "total_requests":   total,
            "blocked_requests": blocked,
            "allowed_requests": total - blocked,
            "avg_latency_ms":   round(avg_lat, 2) if avg_lat else 0.0,
            "window_minutes":   since_minutes,
        }

    @staticmethod
    def top_ips(db: Session, limit: int = 10, since_minutes: int = 60) -> list[dict]:
        since = datetime.utcnow() - timedelta(minutes=since_minutes)
        rows = (
            db.query(RequestLog.client_ip, func.count(RequestLog.id).label("count"))
            .filter(RequestLog.created_at >= since)
            .group_by(RequestLog.client_ip)
            .order_by(func.count(RequestLog.id).desc())
            .limit(limit)
            .all()
        )
        return [{"ip": r.client_ip, "count": r.count} for r in rows]
