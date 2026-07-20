"""
Placeholder priority engine.

Maps category -> P1..P4 using a fixed table. This is intentionally simple and
is a stand-in for the real impact/urgency matrix the SME meeting is expected
to produce (see app/config.py TICKET_PRIORITY_BY_CATEGORY). Swap the table,
not the call sites, once that matrix is defined.
"""

from app.config import TICKET_DEFAULT_PRIORITY, TICKET_PRIORITY_BY_CATEGORY


def determine_priority(category: str) -> str:
    return TICKET_PRIORITY_BY_CATEGORY.get((category or "").upper(), TICKET_DEFAULT_PRIORITY)
