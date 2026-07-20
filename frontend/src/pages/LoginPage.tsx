import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import type { Technician } from "../api/types";

// Placeholder identity screen. Replace with Microsoft Entra ID SSO before
// production - see PROJECT discussion: frontend identity should be the same
// AAD identity the bot already sees in Teams, otherwise a manual mapping
// table between "web login" and "Teams user" will drift out of sync.
export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [technicians, setTechnicians] = useState<Technician[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listTechnicians()
      .then((t) => {
        setTechnicians(t);
        if (t.length) setSelected(t[0].id);
      })
      .catch((err) => setError(err.message));
  }, []);

  const loginAsTechnician = async () => {
    const tech = technicians.find((t) => t.id === selected);
    if (!tech) return;
    // Technicians default to Away (set server-side on seed / on sign-out) and
    // only become Available once they actually sign in here.
    try {
      await api.setTechnicianAvailability(tech.id, true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to set availability");
      return;
    }
    login({ role: "technician", technicianId: tech.id, technicianName: tech.name });
    navigate("/technician");
  };

  const loginAsManager = () => {
    login({ role: "manager" });
    navigate("/manager");
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-900 text-sm font-bold text-white">
            IT
          </div>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">IT Support Board</h1>
            <p className="text-xs text-slate-500">Ticket queue &amp; assignment</p>
          </div>
        </div>

        <div className="mb-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 ring-1 ring-inset ring-amber-200">
          Demo sign-in. Production will use Microsoft Entra ID SSO tied to the
          same identity the bot sees in Teams.
        </div>

        {error && (
          <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            Could not load technicians: {error}
          </p>
        )}

        <label className="mb-1.5 block text-sm font-medium text-slate-700">
          Sign in as technician
        </label>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="mb-3 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          {technicians.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
        <button
          onClick={loginAsTechnician}
          disabled={!selected}
          className="mb-4 w-full rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          Continue as technician
        </button>

        <div className="my-4 flex items-center gap-3">
          <div className="h-px flex-1 bg-slate-200" />
          <span className="text-xs text-slate-400">or</span>
          <div className="h-px flex-1 bg-slate-200" />
        </div>

        <button
          onClick={loginAsManager}
          className="w-full rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Continue as manager
        </button>
      </div>
    </div>
  );
}
