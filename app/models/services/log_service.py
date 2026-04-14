import asyncio
import logging

from sqlalchemy.orm import Session

from app.models.request_log import RequestLog
from app.models.repositories.log_repository import LogRepository

logger = logging.getLogger(__name__)


class LogService:
    """
    Persists request logs asynchronously (fire-and-forget).

    S — responsible only for log creation and cleanup scheduling.
        Does not format, does not decide what to log — it just stores.
    """

    @staticmethod
    def record_async(
        db_factory,
        client_ip:   str,
        method:      str,
        path:        str,
        upstream:    str,
        status_code: int,
        latency_ms:  float,
        blocked:     bool        = False,
        blocked_by:  str | None  = None,
    ) -> None:
        """
        Schedule a non-blocking log write.
        The caller gets its response immediately; the DB write happens after.
        """
        asyncio.create_task(
            LogService._write(
                db_factory, client_ip, method, path,
                upstream, status_code, latency_ms, blocked, blocked_by,
            )
        )

    @staticmethod
    async def _write(
        db_factory,
        client_ip:   str,
        method:      str,
        path:        str,
        upstream:    str,
        status_code: int,
        latency_ms:  float,
        blocked:     bool,
        blocked_by:  str | None,
    ) -> None:
        db: Session = db_factory()
        try:
            log = RequestLog(
                client_ip=client_ip,
                method=method,
                path=path,
                upstream=upstream,
                status_code=status_code,
                latency_ms=latency_ms,
                blocked=blocked,
                blocked_by=blocked_by,
            )
            LogRepository.create(db, log)
        except Exception as exc:
            logger.error("Failed to write log: %s", exc)
        finally:
            db.close()

    @staticmethod
    def purge_old(db: Session, retention_days: int) -> int:
        deleted = LogRepository.delete_older_than(db, retention_days)
        logger.info("Purged %d logs older than %d days", deleted, retention_days)
        return deleted
