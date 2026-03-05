import React from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../app/AuthContext.jsx";
import { API_BASE } from "../services/api.js";
import "./TransformationPage.css";

function getAccountKey(user){
  const id = user?.id || user?._id;
  const email = (user?.email || "").toLowerCase().trim();
  return (id ? String(id) : (email || "guest"));
}

function isBadPersistentUrl(u){
  return !!u && typeof u === "string" && u.startsWith("blob:");
}

function safeJsonParse(s){
  try{ return JSON.parse(s); }catch{ return null; }
}

function fileToDataUrl(file){
  return new Promise((resolve, reject)=>{
    const fr = new FileReader();
    fr.onload = ()=> resolve(String(fr.result || ""));
    fr.onerror = ()=> reject(fr.error || new Error("file read error"));
    fr.readAsDataURL(file);
  });
}

function resolveAssetUrl(u){
  if (!u) return "";
  if (u.startsWith("http://") || u.startsWith("https://")) return u;
  // backend static
  if (u.startsWith("/")) return `${API_BASE}${u}`;
  return u;
}

export default function TransformationPage(){
  const { user } = useAuth();
  const nav = useNavigate();
  const accountKey = React.useMemo(()=>getAccountKey(user), [user]);

  const KEY = React.useMemo(()=>`ps_transform_v1:${accountKey}`, [accountKey]);
  const PRINTS_KEY = React.useMemo(()=>`ps_print_design_v1:${accountKey}`, [accountKey]);

  const [baseUrl, setBaseUrl] = React.useState("");
  const [refUrl, setRefUrl] = React.useState(""); // fabric/photo/pattern
  const [mode, setMode] = React.useState("recolor"); // recolor | pattern | likePhoto
  const [target, setTarget] = React.useState("main"); // main | inserts | both
  const [includeInserts, setIncludeInserts] = React.useState(true);

  const [colorMain, setColorMain] = React.useState("#6dd5c8");
  const [colorInserts, setColorInserts] = React.useState("#a78bfa");

  const [history, setHistory] = React.useState([]); // [{id,url,ts,meta}]
  const [busy, setBusy] = React.useState(false);
  const [status, setStatus] = React.useState("");

  // hydrate
  React.useEffect(()=>{
    const st = safeJsonParse(localStorage.getItem(KEY) || "");
    if (st){
      if (st.baseUrl && !isBadPersistentUrl(st.baseUrl)) setBaseUrl(st.baseUrl);
      if (st.refUrl && !isBadPersistentUrl(st.refUrl)) setRefUrl(st.refUrl);
      if (st.mode) setMode(st.mode);
      if (st.target) setTarget(st.target);
      if (typeof st.includeInserts === "boolean") setIncludeInserts(st.includeInserts);
      if (st.colorMain) setColorMain(st.colorMain);
      if (st.colorInserts) setColorInserts(st.colorInserts);
      if (Array.isArray(st.history)) setHistory(st.history.filter(x=>x?.url && !isBadPersistentUrl(x.url)));
    }
  }, [KEY]);

  // persist
  React.useEffect(()=>{
    try{
      localStorage.setItem(KEY, JSON.stringify({
        v:1,
        baseUrl,
        refUrl,
        mode,
        target,
        includeInserts,
        colorMain,
        colorInserts,
        history
      }));
    }catch{}
  }, [KEY, baseUrl, refUrl, mode, target, includeInserts, colorMain, colorInserts, history]);

  const pullFromPrints = ()=>{
    const st = safeJsonParse(localStorage.getItem(PRINTS_KEY) || "");
    if (!st) { setStatus("Нет данных из «Принты/дизайн»"); return; }
    const last = Array.isArray(st.history) && st.history.length ? st.history[st.history.length-1]?.url : "";
    const u = (last || st.baseUrl || "").trim();
    if (!u || isBadPersistentUrl(u)) { setStatus("В «Принты/дизайн» пока нет результата"); return; }
    setBaseUrl(u);
    setStatus("Подтянул изображение из «Принты/дизайн»");
  };

  const onPickBase = async (file)=>{
    if (!file) return;
    setBusy(true);
    try{
      const d = await fileToDataUrl(file);
      setBaseUrl(d);
      setStatus("Фото загружено");
    }catch(e){
      setStatus("Ошибка чтения файла");
    }finally{
      setBusy(false);
    }
  };

  const onPickRef = async (file)=>{
    if (!file) return;
    setBusy(true);
    try{
      const d = await fileToDataUrl(file);
      setRefUrl(d);
      setStatus("Референс загружен");
    }catch(e){
      setStatus("Ошибка чтения файла");
    }finally{
      setBusy(false);
    }
  };

  const applyMock = async ()=>{
    if (!baseUrl){ setStatus("Сначала загрузите фото изделия"); return; }
    setBusy(true);
    setStatus("Применяю (mock)…");
    try{
      // MOCK: пока движка нет — считаем, что получаем «новый результат».
      // Чтобы пользователь видел прогресс — добавляем запись в историю (url тот же).
      const id = "tr_" + Date.now().toString(36);
      const meta = { mode, target, includeInserts, colorMain, colorInserts, hasRef: !!refUrl };
      const next = { id, url: baseUrl, ts: Date.now(), meta };
      setHistory(h=>[next, ...h].slice(0, 30));
      setStatus("Готово (mock). Дальше подключим движок/маску.");
    }finally{
      setBusy(false);
    }
  };

  const clearAll = ()=>{
    setBaseUrl("");
    setRefUrl("");
    setHistory([]);
    setStatus("Очищено");
  };

  return (
    <div className="trPage">
      <div className="trHeader">
        <div>
          <div className="trTitle">Трансформация</div>
          <div className="trSub">Перекрас / ткань / сублимация (v1 mock) • строгая цель: та же вещь, меняем только материал/цвет</div>
        </div>
        <div className="trHeaderActions">
          <button className="trBtn" onClick={()=>nav("/prints")}>← Принты и дизайн</button>
          <button className="trBtn" onClick={pullFromPrints} disabled={busy}>Взять из Принты/дизайн</button>
          <button className="trBtn danger" onClick={clearAll} disabled={busy}>Очистить</button>
        </div>
      </div>

      <div className="trGrid">
        <div className="trStage card">
          <div className="trStageTop">
            <div className="trTip">Tip: здесь позже будет авто-маска одежды + зоны (наружная ткань / вставки / капюшон / манжеты и т.д.)</div>
            <label className="trBtn fileBtn">
              Загрузить фото
              <input type="file" accept="image/*" onChange={(e)=>onPickBase(e.target.files?.[0])} disabled={busy}/>
            </label>
          </div>

          <div className="trCanvas">
            {baseUrl ? (
              <img className="trImg" src={resolveAssetUrl(baseUrl)} alt="base"/>
            ) : (
              <div className="trEmpty">Загрузите фото изделия или нажмите «Взять из Принты/дизайн»</div>
            )}
          </div>

          {status ? <div className="trStatus">{status}</div> : null}
        </div>

        <div className="trSide">
          <div className="card trBlock">
            <div className="trBlockTitle">Референс ткани / паттерна</div>
            <div className="trRefRow">
              <div className="trRefBox">
                {refUrl ? <img className="trRefImg" src={resolveAssetUrl(refUrl)} alt="ref"/> : <div className="trRefEmpty">нет</div>}
              </div>
              <div className="trRefBtns">
                <label className="trBtn fileBtn small">
                  + Загрузить
                  <input type="file" accept="image/*" onChange={(e)=>onPickRef(e.target.files?.[0])} disabled={busy}/>
                </label>
                <button className="trBtn small" onClick={()=>setRefUrl("")} disabled={busy || !refUrl}>Убрать</button>
              </div>
            </div>
            <div className="trHint">Можно сфоткать кусок ткани с рынка или загрузить цифровой паттерн (в будущем — поддержка лекал/частей).</div>
          </div>

          <div className="card trBlock">
            <div className="trBlockTitle">Тип трансформации</div>
            <div className="trPills">
              <button className={"trPill "+(mode==="recolor"?"on":"")} onClick={()=>setMode("recolor")} disabled={busy}>Перекрас</button>
              <button className={"trPill "+(mode==="pattern"?"on":"")} onClick={()=>setMode("pattern")} disabled={busy}>Паттерн / ткань</button>
              <button className={"trPill "+(mode==="likePhoto"?"on":"")} onClick={()=>setMode("likePhoto")} disabled={busy}>Как на фото</button>
            </div>

            <div className="trRow">
              <div className="trRowLabel">Куда применять</div>
              <select className="trSelect" value={target} onChange={(e)=>setTarget(e.target.value)} disabled={busy}>
                <option value="main">Основная ткань</option>
                <option value="inserts">Вставки</option>
                <option value="both">Основа + вставки</option>
              </select>
            </div>

            <label className="trCheck">
              <input type="checkbox" checked={includeInserts} onChange={(e)=>setIncludeInserts(e.target.checked)} disabled={busy}/>
              Включать вставки (если движок их найдёт)
            </label>

            <div className="trColors">
              <div className="trColor">
                <div className="trColorLabel">Основа</div>
                <input type="color" value={colorMain} onChange={(e)=>setColorMain(e.target.value)} disabled={busy}/>
              </div>
              <div className="trColor">
                <div className="trColorLabel">Вставки</div>
                <input type="color" value={colorInserts} onChange={(e)=>setColorInserts(e.target.value)} disabled={busy}/>
              </div>
            </div>

            <button className="trDo" onClick={applyMock} disabled={busy}>ПРИМЕНИТЬ</button>
          </div>

          <div className="card trBlock">
            <div className="trBlockTitle">История</div>
            {history.length ? (
              <div className="trHistory">
                {history.map(h=>(
                  <button key={h.id} className="trHistItem" onClick={()=>setBaseUrl(h.url)} title="Сделать базой">
                    <img src={resolveAssetUrl(h.url)} alt="" />
                    <div className="trHistMeta">
                      <div className="trHistLine">{new Date(h.ts).toLocaleString()}</div>
                      <div className="trHistLine small">{h.meta?.mode || ""}{h.meta?.hasRef?" +ref":""}</div>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="trEmptySmall">После «ПРИМЕНИТЬ» тут появятся результаты.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
