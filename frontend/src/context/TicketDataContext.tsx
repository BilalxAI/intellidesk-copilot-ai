import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import type { Ticket, Technician, LiveEvent } from "../api/types";

interface TicketDataValue {
  tickets: Ticket[];
  technicians: Technician[];
  loading: boolean;
  error: string | null;
  connected: boolean;
  refresh: () => Promise<void>;
}

const TicketDataContext = createContext<TicketDataValue | undefined>(undefined);

// Live updates come from the backend's SSE stream (/tickets/stream). On any
// event we simply refetch both lists rather than trying to patch state piece
// by piece - the backend is the source of truth and refetching keeps this
// dashboard's data model dead simple. A poll fallback covers the case where
// the SSE connection silently drops (proxies, sleep, etc.).
const POLL_FALLBACK_MS = 20000;

export function TicketDataProvider({ children }: { children: ReactNode }) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [t, tech] = await Promise.all([
        api.listTickets(),
        api.listTechnicians(),
      ]);
      setTickets(t);
      setTechnicians(tech);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();

    const source = new EventSource(api.streamUrl());
    sourceRef.current = source;
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (evt) => {
      try {
        JSON.parse(evt.data) as LiveEvent;
        refresh();
      } catch {
        // ignore malformed event
      }
    };

    const poll = setInterval(refresh, POLL_FALLBACK_MS);

    return () => {
      source.close();
      clearInterval(poll);
    };
  }, [refresh]);

  return (
    <TicketDataContext.Provider
      value={{ tickets, technicians, loading, error, connected, refresh }}
    >
      {children}
    </TicketDataContext.Provider>
  );
}

export function useTicketData() {
  const ctx = useContext(TicketDataContext);
  if (!ctx) throw new Error("useTicketData must be used within TicketDataProvider");
  return ctx;
}
