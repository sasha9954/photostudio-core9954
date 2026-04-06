import React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { authLogin, authRegister } from "../services/authApi.js";
import { useAuth } from "../app/AuthContext.jsx";

export default function LoginPage(){
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const { user, refresh } = useAuth();

  const [mode, setMode] = React.useState("login"); // login | register
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPwd, setShowPwd] = React.useState(false);
  const [err, setErr] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const returnTo = sp.get("returnTo") || "/home";

  React.useEffect(()=>{
    if(user) nav(returnTo);
  },[user, nav, returnTo]);

  async function submit(e){
    e.preventDefault();
    setErr("");

    const emailN = (email || "").trim();
    if(!emailN || !emailN.includes("@")){
      setErr("Введите корректный email");
      return;
    }
    if(!password || password.length < 6){
      setErr("Пароль должен быть минимум 6 символов");
      return;
    }

    setBusy(true);
    try{
      if(mode==="login"){
        await authLogin({ email, password });
      }else{
        await authRegister({ email, password, name: name || email.split("@")[0] });
      }
      const me = await refresh();
      if(!me?.ok){
        throw new Error("Сессия не активировалась. Проверь сетевой конфиг: фронт должен ходить в backend через VITE_API_BASE_URL и открываться с разрешённого origin. Обнови страницу и попробуй ещё раз.");
      }
      nav(returnTo);
    }catch(ex){
      const raw = String(ex?.message || ex || "");
      // Дружественные русские ошибки
      if(raw.toLowerCase().includes("unique") || raw.toLowerCase().includes("already")){
        setErr("Этот email уже зарегистрирован");
      }else if(raw.toLowerCase().includes("invalid") || raw.toLowerCase().includes("credentials")){
        setErr("Неверный email или пароль");
      }else{
        setErr(raw || "Ошибка. Попробуйте ещё раз");
      }
    }finally{
      setBusy(false);
    }
  }

  return (
    <div className="pageCard authCard" style={{ maxWidth: 560 }}>
      <h1 className="pageTitle">Вход / Регистрация</h1>
      <p className="pageSubtitle">В гостевом режиме доступен только просмотр. Войдите, чтобы открыть все разделы.</p>

      <div className="authTabs">
        <button type="button" className={"btn " + (mode==="login" ? "" : "btnGhost")} onClick={()=>setMode("login")}>Вход</button>
        <button type="button" className={"btn " + (mode==="register" ? "" : "btnGhost")} onClick={()=>setMode("register")}>Регистрация</button>
      </div>

      <form onSubmit={submit} className="authForm">
        <label className="field">
          <div className="fieldLabel">Email</div>
          <input className="input" value={email} onChange={(e)=>setEmail(e.target.value)} placeholder="you@mail.com" required />
        </label>

        {mode==="register" ? (
          <label className="field">
            <div className="fieldLabel">Имя</div>
            <input className="input" value={name} onChange={(e)=>setName(e.target.value)} placeholder="Sasha" />
          </label>
        ) : null}

        <label className="field">
          <div className="fieldLabel">Пароль</div>
          <div className="inputWithIcon">
            <input
              className="input"
              type={showPwd ? "text" : "password"}
              value={password}
              onChange={(e)=>setPassword(e.target.value)}
              placeholder="••••••••"
              required
            />
            <button
              type="button"
              className="inputIconBtn"
              onClick={()=>setShowPwd(v=>!v)}
              aria-label={showPwd ? "Скрыть пароль" : "Показать пароль"}
              title={showPwd ? "Скрыть пароль" : "Показать пароль"}
            >
              {showPwd ? "🙈" : "👁"}
            </button>
          </div>
        </label>

        {err ? <div className="errorBox">Ошибка: {err}</div> : null}

        <button className="btn" disabled={busy}>{busy ? "..." : (mode==="login" ? "Войти" : "Создать аккаунт")}</button>
      </form>
    </div>
  );
}
