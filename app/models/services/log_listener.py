"""
LogListener — subscribes to gateway domain events and persists them.

S — handles only the "write a log entry when something happened" concern.
D — depends on EventManager (abstraction) and LogService (single concrete collaborator).
"""

from app.core.event_manager import EventManager
from app.core.events import RequestBlocked, RequestForwarded
from app.models.services.log_service import LogService


class LogListener:
    """
    Bridges the event bus and the persistence layer.
    Registered once at startup; GatewayController never imports LogService.
    """

    def __init__(self, db_factory, events: EventManager) -> None:
        self._dbf = db_factory
        events.subscribe(RequestBlocked,   self._on_blocked)
        events.subscribe(RequestForwarded, self._on_forwarded)

    def _on_blocked(self, event: RequestBlocked) -> None:
        LogService.record_async(
            self._dbf,
            client_ip=event.client_ip,
            method=event.method,
            path=event.path,
            upstream=event.upstream,
            status_code=event.status_code,
            latency_ms=event.latency_ms,
            blocked=True,
            blocked_by=event.reason,
        )

    def _on_forwarded(self, event: RequestForwarded) -> None:
        LogService.record_async(
            self._dbf,
            client_ip=event.client_ip,
            method=event.method,
            path=event.path,
            upstream=event.upstream,
            status_code=event.status_code,
            latency_ms=event.latency_ms,
        )
