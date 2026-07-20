import type { TicketStatus } from "../api/types";

const PRIORITY_STYLES: Record<string, string> = {
  P1: "bg-red-100 text-red-700 ring-red-600/20",
  P2: "bg-orange-100 text-orange-700 ring-orange-600/20",
  P3: "bg-amber-100 text-amber-700 ring-amber-600/20",
  P4: "bg-slate-100 text-slate-600 ring-slate-500/20",
};

export function PriorityBadge({ priority }: { priority: string }) {
  const style = PRIORITY_STYLES[priority] || PRIORITY_STYLES.P4;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}
    >
      {priority}
    </span>
  );
}

const STATUS_STYLES: Record<TicketStatus, string> = {
  New: "bg-blue-100 text-blue-700 ring-blue-600/20",
  Assigned: "bg-indigo-100 text-indigo-700 ring-indigo-600/20",
  InProgress: "bg-amber-100 text-amber-800 ring-amber-600/20",
  Resolved: "bg-emerald-100 text-emerald-700 ring-emerald-600/20",
  Closed: "bg-slate-100 text-slate-500 ring-slate-500/20",
  Reopened: "bg-rose-100 text-rose-700 ring-rose-600/20",
};

const STATUS_LABELS: Record<TicketStatus, string> = {
  New: "New",
  Assigned: "Assigned",
  InProgress: "In Progress",
  Resolved: "Resolved",
  Closed: "Closed",
  Reopened: "Reopened",
};

export function StatusBadge({ status }: { status: TicketStatus }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.New;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}
    >
      {STATUS_LABELS[status] || status}
    </span>
  );
}
