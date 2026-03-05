import React from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { getStudioByKey } from "./studiosData.js";
import LookbookPage from "./LookbookPage.jsx";
import ClipStoryboardPage from "./ClipStoryboardPage.jsx";
import { useAuth } from "../app/AuthContext.jsx";

export default function StudioHostPage() {
  const { key } = useParams();
  const nav = useNavigate();
  const loc = useLocation();
  const { user } = useAuth();

  const studio = getStudioByKey(key);

  // Ensure entering Lookbook from anywhere preserves mode (especially from /studios).
  const isLookbook = studio?.key === "lookbook";
  const sp = React.useMemo(() => new URLSearchParams(loc.search || ""), [loc.search]);
  const modeParam = (sp.get("mode") || "").toUpperCase();

  React.useEffect(() => {
    if (!isLookbook) return;
    if (modeParam) return;

    const accountKey = user?.id || "guest";
    const last = (localStorage.getItem(`ps_lb_lastMode_v1:${accountKey}`) || "TORSO").toUpperCase();
    nav(`/studio/lookbook?mode=${encodeURIComponent(last)}`, { replace: true });
  }, [isLookbook, modeParam, nav, user?.id]);

  if (!studio) {
    return (
      <div className="page">
        <div className="pageCard">
          <h1 className="pageTitle">Студия не найдена</h1>
          <p className="pageSubtitle">Похоже, ссылка неверная.</p>
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn" onClick={() => nav("/studios")}>К студиям</button>
          </div>
        </div>
      </div>
    );
  }

  if (studio.key === "lookbook") {
    // While we normalize URL to include ?mode=..., render nothing to avoid flicker.
    if (!modeParam) return null;
    return <LookbookPage />;
  }

  if (studio.key === "storyboard") {
    return <ClipStoryboardPage />;
  }

  const returnTo = encodeURIComponent(`/studio/${studio.key}`);

  return (
    <div className="page">
      <div className="pageCard">
        <h1 className="pageTitle">{studio.title}</h1>
        <p className="pageSubtitle">{studio.desc}</p>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 12 }}>
          <button
            className="btn"
            type="button"
            onClick={() => nav(`/scene?returnTo=${returnTo}`)}
          >
            Собрать / обновить сцену
          </button>
          <button className="btn" type="button" onClick={() => nav("/studios")}>К списку студий</button>
        </div>

        <div style={{ marginTop: 16, color: "rgba(255,255,255,0.55)", fontSize: 13 }}>
          Пока это заглушка студии. Следующий шаг — подключить использование сцены в конкретной студии.
        </div>
      </div>
    </div>
  );
}
