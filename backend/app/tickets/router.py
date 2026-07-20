"""
Ticket + technician REST API.

This is the contract a future frontend (technician dashboard, manager
reporting view) is meant to consume. Kept intentionally small: create,
read, list, and status transitions. Realtime push (websocket/SSE) is not
implemented here yet - the frontend would poll these endpoints first, and a
push layer can be added on top without changing this contract.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.notifier import notify
from . import events
from .assignment import create_and_assign_ticket, reassign_pending_tickets
from .store import get_ticket_store

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_STATUSES = {"New", "Assigned", "InProgress", "Resolved", "Closed", "Reopened"}

# Status changes that should proactively message the end user in Teams.
# Deliberately narrow: internal transitions (e.g. New -> Assigned) are not
# noise the user needs to see; only these two matter to them day-to-day.
USER_NOTIFY_STATUSES = {"Assigned", "Resolved"}


class CreateTicketRequest(BaseModel):
    conversation_id: str
    user_id: str
    user_name: Optional[str] = None
    category: str
    issue: str = Field(..., min_length=1, max_length=2000)


class UpdateStatusRequest(BaseModel):
    status: str
    changed_by: str = Field(..., description="Technician id, or 'user' / 'system'")


class AvailabilityRequest(BaseModel):
    available: bool


@router.get("/tickets/stream")
async def tickets_stream(request: Request):
    """Server-Sent Events feed of ticket/technician changes for live dashboards."""
    queue = events.subscribe()
    logger.debug("SSE: client subscribed (%s)", request.client)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            logger.debug("SSE: client unsubscribed (%s)", request.client)
            events.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/tickets")
async def create_ticket(request: CreateTicketRequest):
    result = create_and_assign_ticket(
        conversation_id=request.conversation_id,
        user_id=request.user_id,
        user_name=request.user_name,
        category=request.category,
        issue=request.issue,
    )
    if result.get("status") == "Assigned":
        _notify_user_of_assignment(result)
    # create_and_assign_ticket() already publishes the SSE event itself
    # (shared by this endpoint and the chat/Teams escalation path).
    return result


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    store = get_ticket_store()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.get("/tickets")
async def list_tickets(status: Optional[str] = None):
    store = get_ticket_store()
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {sorted(VALID_STATUSES)}")
    return store.list_tickets(status=status)


@router.get("/tickets/{ticket_id}/history")
async def get_ticket_history(ticket_id: str):
    store = get_ticket_store()
    if not store.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return store.get_history(ticket_id)


@router.post("/tickets/{ticket_id}/status")
async def update_ticket_status(ticket_id: str, request: UpdateStatusRequest):
    store = get_ticket_store()
    if request.status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {sorted(VALID_STATUSES)}")
    if not store.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")

    try:
        ticket = store.update_ticket_status(ticket_id, to_status=request.status, changed_by=request.changed_by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if request.status in USER_NOTIFY_STATUSES:
        _notify_user_of_status(ticket, request.status)

    events.publish("ticket_updated", ticket)
    return ticket


@router.post("/tickets/{ticket_id}/reopen")
async def reopen_ticket(ticket_id: str, request: UpdateStatusRequest):
    store = get_ticket_store()
    if not store.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        ticket = store.reopen_ticket(ticket_id, changed_by=request.changed_by)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    events.publish("ticket_updated", ticket)
    return ticket


@router.get("/technicians")
async def list_technicians():
    return get_ticket_store().list_technicians()


@router.get("/technicians/{technician_id}/tickets")
async def get_technician_queue(technician_id: str):
    store = get_ticket_store()
    if not store.get_technician(technician_id):
        raise HTTPException(status_code=404, detail="Technician not found")
    return store.list_tickets_for_technician(technician_id)


@router.post("/technicians/{technician_id}/availability")
async def set_technician_availability(technician_id: str, request: AvailabilityRequest):
    store = get_ticket_store()
    try:
        technician = store.set_technician_availability(technician_id, request.available)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    events.publish("technician_updated", technician)

    # A technician going Available may unblock a backlog of tickets that had
    # nobody to assign to when they were created - hand those out now instead
    # of leaving them stuck in New until someone happens to notice.
    if request.available:
        for result in reassign_pending_tickets():
            _notify_user_of_assignment(result)

    return technician


def _notify_user_of_assignment(result: dict) -> None:
    technician = result.get("technician") or {}
    lines = [f"Your ticket (#{result['ticket_id']}) has been assigned to {technician.get('name', 'a technician')}."]
    if result.get("queue_position") is not None:
        lines.append(f"Current queue position: {result['queue_position']}")
    if result.get("eta_minutes") is not None:
        lines.append(f"Estimated response time: ~{result['eta_minutes']} minutes")
    lines.append(f"Priority: {result.get('priority', 'N/A')}")
    conversation_key = result.get("conversation_id") or ""
    if conversation_key:
        notify(conversation_key, "\n".join(lines))


def _notify_user_of_status(ticket: dict, status: str) -> None:
    conversation_key = ticket.get("conversation_id") or ""
    if not conversation_key:
        return
    if status == "Resolved":
        text = (
            f"Your ticket (#{ticket['id']}) has been marked resolved. "
            "Reply here if the issue is still happening and we'll reopen it."
        )
    else:
        text = f"Update on your ticket (#{ticket['id']}): status is now {status}."
    notify(conversation_key, text)
