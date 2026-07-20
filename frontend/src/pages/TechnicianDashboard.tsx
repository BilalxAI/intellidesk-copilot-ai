import { useEffect, useMemo } from "react";
import { Layout } from "../components/Layout";
import { StatCard } from "../components/StatCard";
import { TicketCard } from "../components/TicketCard";
import { useAuth } from "../context/AuthContext";
import { useTicketData } from "../context/TicketDataContext";
import { api } from "../api/client";

export default function TechnicianDashboard() {
  const { session } = useAuth();
  const { tickets, technicians, refresh } = useTicketData();

  const me = technicians.find((t) => t.id === session?.technicianId);

  // Best-effort: if the technician just closes the tab/browser instead of
  // clicking Sign out, flip them back to Away so they don't stay stuck
  // Available (and getting assigned tickets) after they've actually left.
  useEffect(() => {
    const technicianId = session?.technicianId;
    if (!technicianId) return;
    const handler = () => api.setTechnicianAvailabilityBeacon(technicianId, false);
    window.addEventListener("pagehide", handler);
    return () => window.removeEventListener("pagehide", handler);
  }, [session]);

  const myTickets = useMemo(
    () =>
      tickets
        .filter((t) => t.assigned_technician_id === session?.technicianId)
        .filter((t) => t.status === "Assigned" || t.status === "InProgress" || t.status === "Reopened")
        .sort((a, b) => (a.created_at < b.created_at ? -1 : 1)),
    [tickets, session]
  );

  const resolvedToday = useMemo(() => {
    const today = new Date().toDateString();
    return tickets.filter(
      (t) =>
        t.assigned_technician_id === session?.technicianId &&
        (t.status === "Resolved" || t.status === "Closed") &&
        new Date(t.updated_at).toDateString() === today
    ).length;
  }, [tickets, session]);

  const toggleAvailability = async () => {
    if (!me) return;
    await api.setTechnicianAvailability(me.id, !me.available);
    refresh();
  };

  const advance = async (ticketId: string, nextStatus: string) => {
    if (!session?.technicianId) return;
    await api.updateTicketStatus(ticketId, nextStatus, session.technicianId);
    refresh();
  };

  return (
    <Layout title="My Queue" subtitle="Technician dashboard">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="grid flex-1 grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard label="Assigned to me" value={myTickets.length} />
          <StatCard label="Resolved today" value={resolvedToday} tone="success" />
          <StatCard
            label="Status"
            value={me?.available ? "Available" : "Away"}
            tone={me?.available ? "success" : "warning"}
          />
        </div>
        {me && (
          <button
            onClick={toggleAvailability}
            className={`rounded-lg px-4 py-2 text-sm font-medium ${
              me.available
                ? "bg-emerald-600 text-white hover:bg-emerald-700"
                : "bg-slate-200 text-slate-700 hover:bg-slate-300"
            }`}
          >
            {me.available ? "Available — go Away" : "Away — go Available"}
          </button>
        )}
      </div>

      <h2 className="mb-3 text-sm font-semibold text-slate-500">Your open tickets</h2>
      {myTickets.length === 0 ? (
        <div className="rounded-xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-400">
          Nothing assigned right now.
        </div>
      ) : (
        <div className="space-y-3">
          {myTickets.map((t) => (
            <TicketCard key={t.id} ticket={t} showActions onAdvance={advance} />
          ))}
        </div>
      )}
    </Layout>
  );
}
