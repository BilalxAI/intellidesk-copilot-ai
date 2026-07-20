import { useState } from "react";
import type { Ticket, Technician } from "../api/types";
import { PriorityBadge, StatusBadge } from "./Badges";

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

interface TicketCardProps {
  ticket: Ticket;
  technician?: Technician;
  showActions?: boolean;
  onAdvance?: (ticketId: string, nextStatus: string) => Promise<void>;
}

const NEXT_ACTION: Record<string, { label: string; next: string } | undefined> = {
  Assigned: { label: "Start working", next: "InProgress" },
  InProgress: { label: "Mark resolved", next: "Resolved" },
  Reopened: { label: "Start working", next: "InProgress" },
};

export function TicketCard({ ticket, technician, showActions, onAdvance }: TicketCardProps) {
  const [busy, setBusy] = useState(false);
  const action = NEXT_ACTION[ticket.status];

  const handleAdvance = async () => {
    if (!action || !onAdvance) return;
    setBusy(true);
    try {
      await onAdvance(ticket.id, action.next);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:shadow-md">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold text-slate-900">
              #{ticket.id}
            </span>
            <PriorityBadge priority={ticket.priority} />
            <StatusBadge status={ticket.status} />
          </div>
          <p className="mt-1.5 truncate text-sm text-slate-700">{ticket.issue}</p>
          <p className="mt-1 text-xs text-slate-400">
            {ticket.category.replace(/_/g, " ")} · opened {timeAgo(ticket.created_at)}
            {ticket.user_name ? ` · ${ticket.user_name}` : ""}
          </p>
        </div>
        {showActions && action && (
          <button
            disabled={busy}
            onClick={handleAdvance}
            className="shrink-0 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Saving…" : action.label}
          </button>
        )}
        {!showActions && technician && (
          <span className="shrink-0 text-xs text-slate-500">{technician.name}</span>
        )}
      </div>
    </div>
  );
}
