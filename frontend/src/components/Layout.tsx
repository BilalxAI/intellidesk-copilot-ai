import type { ReactNode } from "react";
import { useAuth } from "../context/AuthContext";
import { useTicketData } from "../context/TicketDataContext";
import { api } from "../api/client";

export function Layout({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  const { session, logout } = useAuth();
  const { connected } = useTicketData();

  const handleSignOut = async () => {
    // Signing out always sends a technician back to Away - they only count
    // as Available while actually signed in and working.
    if (session?.role === "technician" && session.technicianId) {
      try {
        await api.setTechnicianAvailability(session.technicianId, false);
      } catch {
        // Best effort - don't block sign-out on this.
      }
    }
    logout();
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div>
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-900 text-sm font-bold text-white">
                IT
              </div>
              <div>
                <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
                {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1.5 text-xs text-slate-500">
              <span
                className={`h-2 w-2 rounded-full ${
                  connected ? "bg-emerald-500" : "bg-slate-300"
                }`}
              />
              {connected ? "Live" : "Reconnecting…"}
            </span>
            {session && (
              <div className="flex items-center gap-3 border-l border-slate-200 pl-4">
                <span className="text-sm text-slate-700">
                  {session.technicianName || "Manager"}
                </span>
                <button
                  onClick={handleSignOut}
                  className="text-sm font-medium text-slate-500 hover:text-slate-900"
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
