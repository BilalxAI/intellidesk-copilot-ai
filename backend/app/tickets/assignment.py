"""
Ticket creation + assignment.

The "find a free technician and assign" step is funneled through a single
background worker thread (AssignmentQueue) so two tickets created in the same
instant can never both grab the same technician. This is the same pattern
already used in app/services/request_queue.py for serializing LLM calls,
applied here to the assignment decision instead. The pending-ticket sweep
(reassign_pending_tickets) goes through the same worker for the same reason -
a ticket becoming assignable when a technician signs in must not race with a
brand new ticket being created at the same instant.

Note: this only makes the *assignment step* race-safe. It does not replace
the need for a real concurrent-safe database (Postgres) once technicians and
a live dashboard are reading/writing this store at the same time.
"""

from __future__ import annotations

import logging
from queue import Queue
from threading import Thread
from concurrent.futures import Future
from typing import Any, Callable, Dict, List, Optional

from . import events
from .priority import determine_priority
from .store import TicketStore, get_ticket_store

logger = logging.getLogger(__name__)


def _pick_technician(
    store: TicketStore, conn, category: str
) -> Optional[Dict[str, Any]]:
    """Pick a technician, skill/capacity-agnostic by design.

    No skill routing, no per-technician capacity ceiling. Every available
    technician is eligible for every category. Whoever currently has the
    fewest open tickets gets it - that's "free" if they have zero, otherwise
    it's whoever is soonest to free up. Ties broken by technician id order
    (stable, so assignment cycles through the list predictably).

    Returns None only if every technician is marked unavailable.
    """
    candidates = [t for t in store.list_technicians() if t["available"]]
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["open_ticket_count"], c["id"]))
    return candidates[0]


def _create_and_assign(payload: Dict[str, Any]) -> Dict[str, Any]:
    store = get_ticket_store()
    category = payload["category"]
    priority = determine_priority(category)

    return store.create_and_assign(
        category=category,
        issue=payload["issue"],
        priority=priority,
        conversation_id=payload.get("conversation_id") or "",
        user_id=payload.get("user_id") or "",
        user_name=payload.get("user_name"),
        pick_technician=_pick_technician,
    )


def _reassign_pending() -> List[Dict[str, Any]]:
    """Assign as many backlogged (unassigned) tickets as there is capacity for.

    Oldest first (FIFO). Stops as soon as no technician is available -
    at that point every remaining pending ticket would fail for the same
    reason, so there's no point scanning further.
    """
    store = get_ticket_store()
    assigned: List[Dict[str, Any]] = []
    for ticket in store.list_unassigned_tickets():
        result = store.assign_existing_ticket(ticket["id"], _pick_technician)
        if result is None:
            break
        assigned.append(result)
    return assigned


class AssignmentQueue:
    """Single-worker queue: only one ticket-assignment decision runs at a time."""

    def __init__(self):
        self._queue: "Queue[tuple[Future, Callable[[], Any]]]" = Queue()
        self._worker = Thread(target=self._worker_loop, name="ticket-assignment-worker", daemon=True)
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            future, task = self._queue.get()
            try:
                result = task()
                if not future.cancelled():
                    future.set_result(result)
            except Exception as exc:  # noqa: BLE001
                logger.error("Ticket assignment task failed: %s", exc, exc_info=True)
                if not future.cancelled():
                    future.set_exception(exc)
            finally:
                self._queue.task_done()

    def run(self, task: Callable[[], Any], timeout_seconds: int = 30) -> Any:
        future: Future = Future()
        self._queue.put((future, task))
        return future.result(timeout=timeout_seconds)


_assignment_queue: Optional[AssignmentQueue] = None


def get_assignment_queue() -> AssignmentQueue:
    global _assignment_queue
    if _assignment_queue is None:
        _assignment_queue = AssignmentQueue()
    return _assignment_queue


def create_and_assign_ticket(
    conversation_id: str,
    user_id: str,
    user_name: Optional[str],
    category: str,
    issue: str,
) -> Dict[str, Any]:
    """Public entry point: create a ticket and assign it, race-safe.

    Publishes the SSE event here (not in the HTTP router) so every caller -
    the chat/Teams escalation flow in pipeline.py, or a direct POST /tickets
    call - triggers a live dashboard update, not just the latter.
    """
    payload = {
        "conversation_id": conversation_id,
        "user_id": user_id,
        "user_name": user_name,
        "category": category,
        "issue": issue,
    }
    result = get_assignment_queue().run(lambda: _create_and_assign(payload))
    events.publish("ticket_created", result)
    return result


def reassign_pending_tickets() -> List[Dict[str, Any]]:
    """Call when a technician goes Available - hands out any backlogged tickets.

    Publishes a ticket_updated event per ticket assigned this way, same as any
    other status change, so the dashboard updates live without a refresh.
    """
    assigned = get_assignment_queue().run(_reassign_pending)
    for result in assigned:
        events.publish("ticket_updated", result)
    return assigned
