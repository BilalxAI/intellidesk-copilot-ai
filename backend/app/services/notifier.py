"""
Decoupled hook for pushing a proactive Teams message back to a user when a
ticket they own changes state (e.g. resolved).

app/main.py registers the actual Bot Framework send implementation at startup
(same "set the real implementation, module stays decoupled" pattern already
used for the request queue's processor). Anything that changes ticket status
just calls notify(); it does not need to import bot_adapter or Bot Framework
types directly.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_notifier: Optional[Callable[[str, str], None]] = None


def set_notifier(fn: Callable[[str, str], None]) -> None:
    global _notifier
    _notifier = fn


def notify(conversation_key: str, text: str) -> None:
    if _notifier is None:
        logger.warning("No notifier registered; dropping message for %s: %s", conversation_key, text[:80])
        return
    try:
        _notifier(conversation_key, text)
    except Exception as exc:  # noqa: BLE001
        logger.error("Notifier failed for %s: %s", conversation_key, exc, exc_info=True)
