import React from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../app/AuthContext.jsx";

const KEY = "ps_sidebar_collapsed_v1";

const Icon = ({ children }) => <span className="sideIcon">{children}</span>;

const NavItem = ({ to, icon, label, soon, protectedRoute, isAuthed, onNeedAuth }) => {
  const locked = protectedRoute && !isAuthed;

  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        "sideItem" +
        (isActive ? " active" : "") +
        (soon ? " soon" : "") +
        (locked ? " locked" : "")
      }
      onClick={(e) => {
        if (soon) {
          e.preventDefault();
          return;
        }
        if (locked) {
          e.preventDefault();
          onNeedAuth?.(to);
        }
      }}
      title={locked ? `${label} (нужен вход)` : label}
    >
      <div className="sideItemInner">
        <Icon>{icon}</Icon>
        <span className="sideLabel">{label}</span>
      </div>
      {soon ? <span className="soonPill">Скоро</span> : null}
      {locked ? <span className="lockPill">🔒</span> : null}
    </NavLink>
  );
};

export default function Sidebar() {
  const nav = useNavigate();
  const { user } = useAuth();

  const [collapsed, setCollapsed] = React.useState(() => {
    try {
      const v = localStorage.getItem(KEY);
      // если ключа нет — стартуем свернутыми
      return v ? v === "1" : true;
    } catch {
      return true;
    }
  });

  React.useEffect(() => {
    try {
      localStorage.setItem(KEY, collapsed ? "1" : "0");
    } catch {}
  }, [collapsed]);

  const isAuthed = !!user;

  function needAuth(to){
    const rt = encodeURIComponent(to);
    nav(`/login?returnTo=${rt}`);
  }

  return (
    <aside className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="sideTop">
        <button className="sideLogo" onClick={() => nav("/home")} title="На главную">
          <span className="logoCircle">ФС</span>
        </button>
        <button className="sideCollapse" onClick={() => setCollapsed((v) => !v)} title="Свернуть">
          {collapsed ? "»" : "«"}
        </button>
      </div>

      <nav className="sideNav">
        <NavItem to="/studios" icon="📷" label="Фото-студии" isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/scene" icon="🧩" label="Создание сцены" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/video" icon="🎬" label="Видео" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/transform" icon="🌀" label="Трансформация" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/models" icon="🧍" label="Модели" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/prints" icon="🖨️" label="Принты" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/studio/storyboard" icon="🧠" label="CLIP / STORYBOARD" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/tryon" icon="👕" label="Примерочная" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />

        <div className="sideSpacer" />

        <NavItem to="/credits" icon="💳" label="Кредиты" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
        <NavItem to="/account" icon="👤" label="Аккаунт" protectedRoute isAuthed={isAuthed} onNeedAuth={needAuth} />
      </nav>
    </aside>
  );
}