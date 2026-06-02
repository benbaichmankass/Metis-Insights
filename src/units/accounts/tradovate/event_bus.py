"""Tiny in-process event bus for order/fill/quote events.

This package is meant to plug into a larger bot. Rather than expose
callbacks tied to the WS client, every event flows through this bus so
the host app can subscribe once and route to its own pipeline.

Synchronous dispatch — handlers run on the producer's thread. Long
work should hand off to a queue. No persistence and no replay; for
debugging history use ``recorder.Recorder``.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, Any


Handler = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        with self._lock:
            self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                self._handlers[topic] = [h for h in self._handlers[topic] if h is not handler]

        return unsubscribe

    def publish(self, topic: str, payload: Any) -> None:
        with self._lock:
            handlers = list(self._handlers.get(topic, ()))
        for h in handlers:
            try:
                h(payload)
            except Exception:  # noqa: BLE001 — bus must not let one bad sub kill the rest
                pass
