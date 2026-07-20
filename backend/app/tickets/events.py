"""
In-process pub/sub for ticket/technician changes, consumed by a Server-Sent
Events endpoint so the frontend dashboard can update live instead of polling.

Single-process only (matches the current single-instance deployment). If this
backend ever runs as multiple replicas, this needs to move to a shared
broker (Redis pub/sub, etc.) so events reach subscribers on other instances.

Thread-safety note: ticket creation from the chat/Teams flow runs inside the
request queue's background worker thread (app/services/request_queue.py),
not on FastAPI's event loop thread. asyncio.Queue is not safe to write to
from another thread directly, so publish() dispatches via
loop.call_soon_threadsafe when called off the loop thread (set_loop() is
called once at startup with the running loop).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_subscribers: List["asyncio.Queue[str]"] = []
_loop: Optional[asyncio.AbstractEventLoop] = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> "asyncio.Queue[str]":
    queue: "asyncio.Queue[str]" = asyncio.Queue()
    _subscribers.append(queue)
    return queue


def unsubscribe(queue: "asyncio.Queue[str]") -> None:
    if queue in _subscribers:
        _subscribers.remove(queue)


def _put(queue: "asyncio.Queue[str]", payload: str) -> None:
    try:
        queue.put_nowait(payload)
    except Exception:
        logger.exception("events: failed to enqueue payload")


def publish(event_type: str, data: Dict[str, Any]) -> None:
    payload = json.dumps({"type": event_type, "data": data})
    if _loop is None:
        logger.warning("events.publish called before set_loop(); dropping %s", event_type)
        return
    for queue in list(_subscribers):
        _loop.call_soon_threadsafe(_put, queue, payload)
