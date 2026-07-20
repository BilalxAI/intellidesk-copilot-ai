import { useMemo, useState } from "react";
import { Layout } from "../components/Layout";
import { StatCard } from "../components/StatCard";
import { PriorityBadge, StatusBadge } from "../components/Badges";
import { TicketDetailDrawer } from "../components/TicketDetailDrawer";
import { useTicketData } from "../context/TicketDataContext";
import { api } from "../api/client";
import type { Ticket } from "../api/types";

const OPEN_STATUSES = new Set(["New", "Assigned", "InProgress", "Reopened"]);
const STATUS_FILTERS = ["All", "New", "Assigned", "InProgress", "Resolved", "Closed", "Reopened"];

export default function ManagerDashboard() {
  const { tickets, technicians, refresh } = useTicketData();
  const [statusFilter, setStatusFilter] = useState("All");
  const [selected, setSelected] = useState<Ticket | null>(null);

  const openTickets = tickets.filter((t) => OPEN_STATUSES.has(t.status));
  const byPriority = useMemo(() => {
    const counts: Record<string, number> = { P1: 0, P2: 0, P3: 0, P4: 0 };
    for (const t of openTickets) counts[t.priority] = (counts[t.priority] || 0) + 1;
    return counts;
  }, [openTickets]);

  const filtered = tickets
    .filter((t) => statusFilter === "All" || t.status === statusFilter)
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  const technicianName = (id: string | null) =>
    technicians.find((t) => t.id === id)?.name;

  const toggleAvailability = async (id: string, available: boolean) => {
    await api.setTechnicianAvailability(id, !available);
    refresh();
  };

  return (
    <Layout title="IT Support Board" subtitle="Manager view · all tickets">
      <div className="mb-8 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Open tickets" value={openTickets.length} />
        <StatCard label="P1 / P2 open" value={byPriority.P1 + byPriority.P2} tone="danger" />
        <StatCard
          label="Technicians available"
          value={`${technicians.filter((t) => t.available).length}/${technicians.length}`}
        />
        <StatCard
          label="Resolved (all time)"
          value={tickets.filter((t) => t.status === "Resolved" || t.status === "Closed").length}
          tone="success"
        />
      </div>

      <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-500">Tickets</h2>
            <div className="flex flex-wrap gap-1.5">
              {STATUS_FILTERS.map((s) => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`rounded-full px-3 py-1 text-xs font-medium ${
                    statusFilter === s
                      ? "bg-slate-900 text-white"
                      : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs font-medium uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-2">Ticket</th>
                  <th className="px-4 py-2">Priority</th>
                  <th className="px-4 py-2">Status</th>
                  <th className="px-4 py-2">Assigned to</th>
                  <th className="px-4 py-2">Opened</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.map((t) => (
                  <tr
                    key={t.id}
                    onClick={() => setSelected(t)}
                    className="cursor-pointer hover:bg-slate-50"
                  >
                    <td className="px-4 py-2.5">
                      <span className="font-mono font-medium text-slate-900">#{t.id}</span>
                      <p className="max-w-xs truncate text-xs text-slate-400">{t.issue}</p>
                    </td>
                    <td className="px-4 py-2.5">
                      <PriorityBadge priority={t.priority} />
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge status={t.status} />
                    </td>
                    <td className="px-4 py-2.5 text-slate-600">
                      {technicianName(t.assigned_technician_id) || "—"}
                    </td>
                    <td className="px-4 py-2.5 text-slate-400">
                      {new Date(t.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                      No tickets match this filter.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div>
          <h2 className="mb-3 text-sm font-semibold text-slate-500">Technician workload</h2>
          <div className="space-y-2">
            {technicians.map((t) => (
              <div
                key={t.id}
                className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3"
              >
                <div>
                  <p className="text-sm font-medium text-slate-900">{t.name}</p>
                  <p className="text-xs text-slate-400">{t.open_ticket_count} open ticket(s)</p>
                </div>
                <button
                  onClick={() => toggleAvailability(t.id, t.available)}
                  className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                    t.available
                      ? "bg-emerald-100 text-emerald-700"
                      : "bg-slate-200 text-slate-500"
                  }`}
                >
                  {t.available ? "Available" : "Away"}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      {selected && (
        <TicketDetailDrawer
          ticket={selected}
          technicianName={technicianName(selected.assigned_technician_id)}
          onClose={() => setSelected(null)}
        />
      )}
    </Layout>
  );
}
