"""
Lightweight synchronous publish/subscribe event bus.

O — open for new event types without modifying this class.
D — high-level modules depend on EventManager, never on concrete handlers.
"""

from collections import defaultdict
from typing import Any, Callable, Type


class EventManager:
    """
    Central event bus.  Handlers are called synchronously in subscription order.
    Any handler may schedule async work internally (e.g. asyncio.create_task).
    """

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: Type, handler: Callable[[Any], None]) -> None:
        """Register *handler* to be called whenever *event_type* is emitted."""
        self._handlers[event_type].append(handler)

    def emit(self, event: Any) -> None:
        """Dispatch *event* to all registered handlers for its type."""
        for handler in self._handlers.get(type(event), []):
            handler(event)
