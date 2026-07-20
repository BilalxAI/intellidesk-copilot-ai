import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth, type Role } from "../context/AuthContext";

export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { session } = useAuth();
  if (!session || session.role !== role) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
