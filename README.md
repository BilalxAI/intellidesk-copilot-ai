# AI IT Support Deskflow

An AI-powered, first-tier IT support assistant that talks to users in Microsoft Teams (or over a plain REST API), tries to resolve common issues itself using a knowledge-base-grounded local LLM, and — when it can't — automatically creates a ticket, assigns it to a technician, and pushes live updates to a real-time dashboard. No page refreshes, no manual triage.

## Why this exists

Most "AI helpdesk bot" demos stop at "chatbot answers a question." This project goes further: it's the full loop — **self-service → escalation → ticket → assignment → live tracking → resolution** — the part that actually matters operationally but almost never gets built.

```
User (Teams or API)
    │
    ▼
AI Bot ── tries self-service first (KB-grounded, not hallucinated steps)
    │
    │  unresolved?
    ▼
Ticket created ── priority assigned ── race-safe technician assignment
    │
    ▼
Live technician dashboard (SSE, real-time, zero polling lag)
    │
    ▼
Status pushed back to the user in Teams automatically
```

## What makes this interesting from an engineering standpoint

- **KB-grounded, not hallucinated.** The LLM formats and explains — it never invents troubleshooting steps. All approved steps live in a structured knowledge base; the model's job is presentation and light classification, not being the source of truth. This matters a lot when a small local model (not GPT-4-class) is doing the talking.
- **Deterministic conversation state machine over an SLM.** Follow-up detection, guided step-by-step troubleshooting, escalation confirmation, and "still not working" reopening are all handled with an explicit rules engine layered around the LLM — because a 350M–3B parameter local model isn't reliable enough to trust with multi-turn state tracking alone.
- **Race-safe ticket assignment, by design, not by luck.** Two tickets created in the same instant can never be handed to the same technician — assignment is funneled through a single-worker queue (the same pattern used elsewhere in the codebase to serialize LLM calls), so "who's free" is only ever evaluated by one thread at a time. Load-tested with concurrent ticket bursts to confirm zero double-assignment.
- **Real-time dashboard via Server-Sent Events, not polling.** Ticket creation, status changes, and technician availability all broadcast over an SSE stream. The tricky part: ticket creation from chat happens on a background worker thread, not the API's event loop — so publishing an event safely requires hopping back onto the event loop thread (`call_soon_threadsafe`), a subtlety that's easy to get wrong silently.
- **Automatic backlog recovery.** If every technician is Away when a ticket comes in, it doesn't get dropped — it waits, and the moment anyone signs back in, a sweep automatically hands out the backlog in FIFO order, notifying the user the instant it happens.
- **Proactive messaging back into Teams.** The bot doesn't just reply once — it can message a user later (ticket assigned, ticket resolved) using Bot Framework's proactive-conversation pattern, with conversation references persisted so it still works even if the user isn't actively chatting.

## Architecture

**Backend** — FastAPI, Python
- Local LLM via Ollama (swappable — small models intentionally, since the LLM formats/explains rather than reasons over unstructured knowledge)
- SQLite-backed conversation store (pluggable — MySQL/SQL Server supported for production)
- Ticketing engine: tickets, technicians, full status-history audit trail
- Single-worker assignment queue for race-free technician assignment
- Server-Sent Events for live dashboard updates
- Microsoft Bot Framework integration for Teams

**Frontend** — React, TypeScript, Vite, Tailwind
- Technician dashboard: live queue, one-click status transitions
- Manager dashboard: full ticket board, technician workload, SLA-relevant stats, live filtering
- Real-time via SSE subscription with a polling fallback for resilience

## Tech stack

`Python` `FastAPI` `SQLite` `Ollama (local LLM)` `Microsoft Bot Framework` `React` `TypeScript` `Vite` `TailwindCSS` `Server-Sent Events`

## Status

Actively developed. Current focus: hardening the assignment engine and expanding dashboard reporting.

---

*This is a personal portfolio build. Company-specific configuration, credentials, and identifying details have been removed/replaced with placeholders.*
