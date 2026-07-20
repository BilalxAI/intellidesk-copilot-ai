import { useEffect, useState } from "react";
import type { Ticket, TicketHistoryEntry } from "../api/types";
import { PriorityBadge, StatusBadge } from "./Badges";
import { api } from "../api/client";

export function TicketDetailDrawer({
  ticket,
  technicianName,
  onClose,
}: {
  ticket: Ticket;
  technicianName?: string;
  onClose: () => void;
}) {
  const [history, setHistory] = useState<TicketHistoryEntry[]>([]);

  useEffect(() => {
    api.getTicketHistory(ticket.id).then(setHistory).catch(() => setHistory([]));
  }, [ticket.id]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h2 className="font-mono text-lg font-semibold text-slate-900">#{ticket.id}</h2>
            <div className="mt-1 flex gap-2">
              <PriorityBadge priority={ticket.priority} />
              <StatusBadge status={ticket.status} />
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700">
            ✕
          </button>
        </div>

        <dl className="mb-6 space-y-2 text-sm">
          <div className="flex justify-between">
            <dt className="text-slate-500">Category</dt>
            <dd className="text-slate-900">{ticket.category.replace(/_/g, " ")}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">User</dt>
            <dd className="text-slate-900">{ticket.user_name || ticket.user_id}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">Assigned to</dt>
            <dd className="text-slate-900">{technicianName || "Unassigned"}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-slate-500">Created</dt>
            <dd className="text-slate-900">{new Date(ticket.created_at).toLocaleString()}</dd>
          </div>
        </dl>

        <div className="mb-6 rounded-lg bg-slate-50 p-3 text-sm text-slate-700">{ticket.issue}</div>

        <h3 className="mb-2 text-sm font-semibold text-slate-500">History</h3>
        <ol className="space-y-3 border-l border-slate-200 pl-4">
          {history.map((h) => (
            <li key={h.id} className="relative text-sm">
              <span className="absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full bg-slate-400" />
              <p className="font-medium text-slate-800">
                {h.from_status ? `${h.from_status} → ${h.to_status}` : h.to_status}
              </p>
              <p className="text-xs text-slate-400">
                {new Date(h.created_at).toLocaleString()}
                {h.changed_by ? ` · ${h.changed_by}` : ""}
              </p>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
