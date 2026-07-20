import type { Ticket, TicketHistoryEntry, Technician } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.error || detail;
    } catch {
      // ignore body parse failure, keep statusText
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listTickets: (status?: string) =>
    request<Ticket[]>(`/tickets${status ? `?status=${status}` : ""}`),
  getTicket: (id: string) => request<Ticket>(`/tickets/${id}`),
  getTicketHistory: (id: string) =>
    request<TicketHistoryEntry[]>(`/tickets/${id}/history`),
  updateTicketStatus: (id: string, status: string, changedBy: string) =>
    request<Ticket>(`/tickets/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status, changed_by: changedBy }),
    }),
  reopenTicket: (id: string, changedBy: string) =>
    request<Ticket>(`/tickets/${id}/reopen`, {
      method: "POST",
      body: JSON.stringify({ changed_by: changedBy }),
    }),
  listTechnicians: () => request<Technician[]>("/technicians"),
  getTechnicianQueue: (id: string) =>
    request<Ticket[]>(`/technicians/${id}/tickets`),
  setTechnicianAvailability: (id: string, available: boolean) =>
    request<Technician>(`/technicians/${id}/availability`, {
      method: "POST",
      body: JSON.stringify({ available }),
    }),
  streamUrl: () => `${API_BASE}/tickets/stream`,
  // Best-effort: fetch() is not guaranteed to complete during page unload,
  // so tab-close / browser-close uses sendBeacon instead. Not as reliable as
  // an explicit Sign out click, but covers the common "just closed the tab"
  // case so a technician doesn't stay stuck Available indefinitely.
  setTechnicianAvailabilityBeacon: (id: string, available: boolean) => {
    const blob = new Blob([JSON.stringify({ available })], {
      type: "application/json",
    });
    navigator.sendBeacon(`${API_BASE}/technicians/${id}/availability`, blob);
  },
};
