import React from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import ShellLayout from "../layout/ShellLayout.jsx";
import SplashPage from "../pages/SplashPage.jsx";
import HomePage from "../pages/HomePage.jsx";
import LoginPage from "../pages/LoginPage.jsx";
import CreditsPage from "../pages/CreditsPage.jsx";
import ScenePage from "../pages/ScenePage.jsx";
import VideoPage from "../pages/VideoPage.jsx";
import PrintsPage from "../pages/PrintsPage.jsx";
import StudiosPage from "../pages/StudiosPage.jsx";
import StudioHostPage from "../pages/StudioHostPage.jsx";
import AccountPage from "../pages/AccountPage.jsx";
import { useAuth } from "./AuthContext.jsx";

const Placeholder = ({ title }) => (
  <div className="pageCard">
    <h1 className="pageTitle">{title}</h1>
    <p className="pageSubtitle">Страница будет добавлена позже.</p>
  </div>
);

function RequireAuth({ children }){
  const { user, loading } = useAuth();
  const loc = useLocation();

  if(loading) return null;

  if(!user){
    const rt = encodeURIComponent(loc.pathname + (loc.search || ""));
    return <Navigate to={`/login?returnTo=${rt}`} replace />;
  }

  return children;
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<SplashPage />} />

      <Route element={<ShellLayout />}>
        {/* Public */}
        <Route path="/home" element={<HomePage />} />
        <Route path="/studios" element={<RequireAuth><StudiosPage /></RequireAuth>} />
        <Route path="/studio/:key" element={<RequireAuth><StudioHostPage /></RequireAuth>} />
        <Route path="/login" element={<LoginPage />} />

        {/* Protected */}
        <Route path="/scene" element={<RequireAuth><ScenePage /></RequireAuth>} />
        <Route path="/video" element={<RequireAuth><VideoPage /></RequireAuth>} />
        <Route path="/transform" element={<RequireAuth><Placeholder title="Трансформация" /></RequireAuth>} />
        <Route path="/models" element={<RequireAuth><Placeholder title="Генерация моделей" /></RequireAuth>} />
        <Route path="/prints" element={<RequireAuth><PrintsPage /></RequireAuth>} />
        <Route path="/tryon" element={<RequireAuth><Placeholder title="Примерочная" /></RequireAuth>} />

        <Route path="/credits" element={<RequireAuth><CreditsPage /></RequireAuth>} />
        <Route path="/account" element={<RequireAuth><AccountPage /></RequireAuth>} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}