import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { TicketDataProvider } from "./context/TicketDataContext";
import { RequireRole } from "./components/RequireRole";
import LoginPage from "./pages/LoginPage";
import TechnicianDashboard from "./pages/TechnicianDashboard";
import ManagerDashboard from "./pages/ManagerDashboard";

function HomeRedirect() {
  const { session } = useAuth();
  if (!session) return <Navigate to="/login" replace />;
  return <Navigate to={session.role === "manager" ? "/manager" : "/technician"} replace />;
}

function App() {
  return (
    <AuthProvider>
      <TicketDataProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<HomeRedirect />} />
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/technician"
              element={
                <RequireRole role="technician">
                  <TechnicianDashboard />
                </RequireRole>
              }
            />
            <Route
              path="/manager"
              element={
                <RequireRole role="manager">
                  <ManagerDashboard />
                </RequireRole>
              }
            />
          </Routes>
        </BrowserRouter>
      </TicketDataProvider>
    </AuthProvider>
  );
}

export default App
