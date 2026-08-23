import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import type { ReactNode } from "react";
import { LogOut } from "lucide-react";
import { clearToken, getToken } from "./services/api";
import LoginPage from "./pages/LoginPage";
import ProjectsPage from "./pages/ProjectsPage";
import ProjectWorkspace from "./pages/ProjectWorkspace";

function RequireAuth({ children }: { children: ReactNode }) {
  return getToken() ? children : <Navigate to="/login" replace />;
}

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const logout = () => {
    clearToken();
    navigate("/login");
  };

  return (
    <div className="app-shell">
      {getToken() && !location.pathname.startsWith("/projects/") && (
        <header className="topbar">
          <div>
            <strong>Security CodeWiki</strong>
            <span>Create or open a project</span>
          </div>
          <button className="icon-button" aria-label="Log out" onClick={logout} title="Log out">
            <LogOut size={18} />
          </button>
        </header>
      )}
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<RequireAuth><ProjectsPage /></RequireAuth>} />
        <Route path="/projects/:projectId" element={<RequireAuth><ProjectWorkspace /></RequireAuth>} />
      </Routes>
    </div>
  );
}
