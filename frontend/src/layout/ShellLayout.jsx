import React from "react";
import { Outlet, useNavigate } from "react-router-dom";
import Sidebar from "./Sidebar.jsx";
import Header from "./Header.jsx";
import { AuthProvider } from "../app/AuthContext.jsx";
import { useAuth } from "../app/AuthContext.jsx";
import { fetchJson } from "../services/api.js";

/**
 * Global notifications (toast) + background job watcher.
 * Goal: if a long-running generation finishes while user is on another page,
 * show a nice "ГОТОВО" message with where it was generated + quick navigation.
 *
 * Implementation is intentionally minimal (no refactor across pages).
 */

function emitNotify(detail) {
  try {
    window.dispatchEvent(new CustomEvent("ps:notify", { detail }));
  } catch {}
}

function formatStudioLabel(studioKey) {
  const k = String(studioKey || "").toLowerCase();
  if (k === "lookbook") return "Lookbook";
  if (k === "scene") return "Сцена";
  if (k === "video") return "Видео";
  if (k === "prints" || k === "print" || k === "design") return "Принты и дизайн";
  return studioKey || "Studio";
}


function studioMetaFromSource(source){
  // source can be:
  // - string: "prints" | "lookbook" | ...
  // - object: { studioKey: "prints", mode?: "...", to?: "/prints" }
  // - already formatted string label
  const raw = source;
  let studioKey = null;
  let mode = null;
  let to = null;

  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    studioKey = raw.studioKey || raw.studio || raw.key || null;
    mode = raw.mode || null;
    to = raw.to || raw.route || null;
  } else if (typeof raw === "string") {
    // if looks like JSON, don't show it as-is
    const s = raw.trim();
    if (s.startsWith("{") && s.endsWith("}")) {
      try {
        const obj = JSON.parse(s);
        studioKey = obj?.studioKey || obj?.studio || obj?.key || null;
        mode = obj?.mode || null;
        to = obj?.to || obj?.route || null;
      } catch {
        // ignore
      }
    } else {
      // allow both keys ("prints") and labels ("Принты и дизайн")
      studioKey = s;
    }
  }

  const key = String(studioKey || "").toLowerCase();

  const map = {
    prints: { label: "Принты и дизайн", to: "/prints", tone: "violet" },
    "prints-design": { label: "Принты и дизайн", to: "/prints", tone: "violet" },
    design: { label: "Принты и дизайн", to: "/prints", tone: "violet" },
    lookbook: { label: "Lookbook", to: "/lookbook", tone: "green" },
    scene: { label: "Сцена", to: "/scene", tone: "blue" },
    video: { label: "Видео", to: "/video", tone: "cyan" },
    studios: { label: "Студии", to: "/studios", tone: "gray" },
  };

  const meta = map[key];
  if (meta) {
    return { studioKey: key, label: meta.label, to: to || meta.to, tone: meta.tone, mode: mode ? String(mode).toUpperCase() : null };
  }

  // If studioKey is actually a human label, show as neutral badge
  if (studioKey && typeof studioKey === "string" && studioKey.length <= 32) {
    return { studioKey: "custom", label: studioKey, to: to || null, tone: "gray", mode: mode ? String(mode).toUpperCase() : null };
  }

  return null;
}


function _psToText(v) {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    // Prefer readable objects over [object Object]
    return JSON.stringify(v);
  } catch {
    try { return String(v); } catch { return ""; }
  }
}

function NotificationCenter() {
  const nav = useNavigate();
  const [items, setItems] = React.useState([]);
  const seenRef = React.useRef(new Set());

  const push = React.useCallback((n) => {
    const id = String(n?.id || "").trim() || `n_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    if (seenRef.current.has(id)) return;
    seenRef.current.add(id);

    const node = {
      id,
      kind: n?.kind || "info", // info|success|error
      title: _psToText(n?.title) || "",
      message: _psToText(n?.message) || "",
      studioMeta: studioMetaFromSource(n?.source) || null,
      sourceText: (_psToText(n?.source) || null),
      actions: Array.isArray(n?.actions) ? n.actions : [],
      createdAt: Date.now(),
      ttlMs: Number.isFinite(n?.ttlMs) ? n.ttlMs : 9000,
    };
    setItems((prev) => [node, ...(prev || [])].slice(0, 4));
  }, []);

  React.useEffect(() => {
    const onNotify = (e) => push(e?.detail || {});
    window.addEventListener("ps:notify", onNotify);
    return () => window.removeEventListener("ps:notify", onNotify);
  }, [push]);

  React.useEffect(() => {
    if (!items.length) return;
    const now = Date.now();
    const timers = items.map((it) => {
      const left = Math.max(1000, (it.ttlMs || 9000) - (now - (it.createdAt || now)));
      return setTimeout(() => {
        setItems((prev) => (prev || []).filter((x) => x.id !== it.id));
      }, left);
    });
    return () => timers.forEach((t) => clearTimeout(t));
  }, [items]);

  const close = (id) => setItems((prev) => (prev || []).filter((x) => x.id !== id));

  const runAction = (a, id) => {
    try {
      if (a?.to) {
        nav(String(a.to));
      } else if (typeof a?.onClick === "function") {
        a.onClick();
      }
    } finally {
      if (a?.closeOnClick !== false) close(id);
    }
  };

  if (!items.length) return null;

  return (
    <div className="psToastStack" aria-live="polite" aria-relevant="additions">
      {items.map((it) => (
        <div key={it.id} className={`psToast psToast--${it.kind || "info"}`}>
          <button className="psToast__x" type="button" onClick={() => close(it.id)} aria-label="Закрыть">×</button>
          <div className="psToast__top">
            <div className="psToast__title">{it.title}</div>
            {it.studioMeta ? (
              <div className="psToast__source">
                <span className={`psToast__badge psToast__badge--${it.studioMeta.tone || "gray"}`}>
                  {it.studioMeta.label}
                  {it.studioMeta.mode ? <span className="psToast__badgeMode">· {it.studioMeta.mode}</span> : null}
                </span>
              </div>
            ) : it.sourceText ? (
              <div className="psToast__source">{it.sourceText}</div>
            ) : null}
          </div>
          {it.message ? <div className="psToast__msg">{it.message}</div> : null}
          {it.actions?.length ? (
            <div className="psToast__actions">
              {it.actions.map((a, idx) => (
                <button
                  key={`${it.id}_a${idx}`}
                  className={`psToast__btn ${a?.primary ? "primary" : ""}`}
                  type="button"
                  onClick={() => runAction(a, it.id)}
                >
                  {a?.label || "Ок"}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function GlobalJobWatcher() {
  const { user } = useAuth();
  const accountKey = user?.id || "guest";
  const notifiedRef = React.useRef(new Set());
  const timerRef = React.useRef(null);

  const notifyDone = React.useCallback((jobId, meta) => {
    const studioKey = meta?.studioKey || "lookbook";
    const mode = meta?.mode ? String(meta.mode).toUpperCase() : null;
    const count = Number(meta?.count || 0);
    const title = meta?.title || "Генерация готова";
    const message = meta?.message || (count ? `Готово кадров: ${count}` : "Можно открыть результат.");

    emitNotify({
      id: `job_done:${jobId}`,
      kind: "success",
      title,
      source: `${formatStudioLabel(studioKey)}${mode ? ` · ${mode}` : ""}`,
      message,
      actions: [
        { label: "Перейти", primary: true, to: meta?.to || "/studios" },
        { label: "Закрыть", primary: false },
      ],
      ttlMs: 11000,
    });
  }, []);

  const notifyError = React.useCallback((jobId, meta, msg) => {
    const studioKey = meta?.studioKey || "lookbook";
    const mode = meta?.mode ? String(meta.mode).toUpperCase() : null;
    emitNotify({
      id: `job_err:${jobId}`,
      kind: "error",
      title: meta?.title || "Ошибка генерации",
      source: `${formatStudioLabel(studioKey)}${mode ? ` · ${mode}` : ""}`,
      message: msg || "Не удалось завершить генерацию.",
      actions: [
        { label: "Открыть", primary: true, to: meta?.to || "/studios" },
        { label: "Закрыть", primary: false },
      ],
      ttlMs: 14000,
    });
  }, []);

  React.useEffect(() => {
    const stop = () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };

    const safeLsKeys = () => {
      const out = [];
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k) out.push(k);
        }
      } catch {}
      return out;
    };

    const tick = async () => {
      try {
        // 1) Lookbook jobs: ps_lb_activeJob_v1:<accountKey>:<MODE> -> <jobId>
        const lbPrefix = `ps_lb_activeJob_v1:${accountKey}:`;
        const lbKeys = safeLsKeys().filter((k) => k.startsWith(lbPrefix));

        for (const k of lbKeys) {
          let jobId = null;
          try { jobId = localStorage.getItem(k); } catch {}
          jobId = jobId ? String(jobId).trim() : null;
          if (!jobId) continue;
          if (notifiedRef.current.has(jobId)) continue;

          const mode = String(k.slice(lbPrefix.length) || "").toUpperCase();
          const meta = { studioKey: "lookbook", mode, to: `/studio/lookbook?mode=${mode}` };

          const res = await fetchJson(`/api/lookbook/jobs/${jobId}`);
          const job = res?.job;
          const state = job?.state;

          if (state === "done") {
            const count = Array.isArray(job?.result?.results) ? job.result.results.length : 0;
            notifiedRef.current.add(jobId);
            try { localStorage.removeItem(k); } catch {}
            notifyDone(jobId, { ...meta, count, title: "Фотосессия готова", message: "Готово. Можно открыть результаты." });
          } else if (state === "error") {
            const msg = job?.error || "Ошибка фотосессии";
            notifiedRef.current.add(jobId);
            try { localStorage.removeItem(k); } catch {}
            notifyError(jobId, meta, msg);
          }
        }

        // 2) Scene jobs: ps_sc_activeJob_v1:<accountKey>:<KIND> -> JSON { jobId, action } OR plain <jobId>
        const scPrefix = `ps_sc_activeJob_v1:${accountKey}:`;
        const scKeys = safeLsKeys().filter((k) => k.startsWith(scPrefix));

        for (const k of scKeys) {
          let raw = null;
          try { raw = localStorage.getItem(k); } catch {}
          if (!raw) continue;

          let jobId = null;
          let action = "";
          try {
            const rec = JSON.parse(raw);
            jobId = rec?.jobId ? String(rec.jobId) : null;
            action = rec?.action ? String(rec.action) : "";
          } catch {
            // fallback: old format stores just jobId
            jobId = String(raw).trim();
          }

          if (!jobId) continue;
          if (notifiedRef.current.has(jobId)) continue;

          const kind = String(k.slice(scPrefix.length) || "").toLowerCase(); // model|location
          const mode = kind ? kind.toUpperCase() : null;

          const res = await fetchJson(`/api/scene/jobs/${jobId}`);
          const job = res?.job;
          const state = job?.state;

          const isApply = String(action || job?.action || "").toLowerCase().includes("apply");
          const title = kind === "model"
            ? (isApply ? "Детали модели применены" : "Модель готова")
            : (kind === "location" ? (isApply ? "Детали локации применены" : "Локация готова") : "Сцена готова");

          if (state === "done") {
            notifiedRef.current.add(jobId);
            try { localStorage.removeItem(k); } catch {}
            notifyDone(jobId, {
              studioKey: "scene",
              mode,
              to: "/scene",
              count: 1,
              title,
              message: "Готово. Перейдите в «Создание сцены».",
            });
          } else if (state === "error") {
            const msg = job?.error || "Ошибка сцены";
            notifiedRef.current.add(jobId);
            try { localStorage.removeItem(k); } catch {}
            notifyError(jobId, { studioKey: "scene", mode, to: "/scene", title: "Ошибка сцены" }, msg);
          }
        }

        // 3) Video jobs: ps_video_activeJob_v1:<accountKey> -> JSON { jobId, t, action? }
        const vKey = `ps_video_activeJob_v1:${accountKey}`;
        let vRaw = null;
        try { vRaw = localStorage.getItem(vKey); } catch {}
        if (vRaw) {
          let vJobId = null;
          let vAction = "";
          try {
            const rec = JSON.parse(vRaw);
            vJobId = rec?.jobId ? String(rec.jobId).trim() : null;
            vAction = rec?.action ? String(rec.action) : "";
          } catch {
            // fallback: old format stores just jobId
            vJobId = String(vRaw).trim();
          }

          if (vJobId && !notifiedRef.current.has(vJobId)) {
            const res = await fetchJson(`/api/video/jobs/${vJobId}`);
            const job = res?.job;
            const state = job?.state;
            const act = String(job?.action || vAction || "").toLowerCase();

            if (state === "done") {
              // If user left the Video page, we still want the result to appear in clip slots after return.
              // So we persist job.result.videos into ps_video_clipSlots_v1:<accountKey> here.
              const vids = (job?.result?.videos && Array.isArray(job.result.videos) ? job.result.videos : [])
                .concat(job?.result?.url ? [job.result.url] : [])
                .concat(job?.videos && Array.isArray(job.videos) ? job.videos : [])
                .filter(Boolean)
                .map((u) => String(u).trim())
                .filter((u) => u && !u.startsWith("blob:"));

              if (vids.length) {
                const clipsKey = `ps_video_clipSlots_v1:${accountKey}`;
                try {
    
                  // If it's a merge job — persist ONLY to merged result (do not pollute clip slots).
                  if (act === "merge") {
                    const mergedKey = `ps_video_mergedResult_v1:${accountKey}`;
                    const first = vids[0] || "";
                    if (first) {
                      try { localStorage.setItem(mergedKey, first); } catch {}
                    }
                  } else {
                    // Insert newest clips at the beginning (slot 1), shifting old ones to the right.
                    const prevRaw = localStorage.getItem(clipsKey) || "[]";
                    const prevArr = JSON.parse(prevRaw);
                    const prev = Array.isArray(prevArr) ? prevArr.filter(Boolean).map(String) : [];
                    const uniqNew = vids.filter((u) => u && !u.startsWith("blob:"));

                    // Remove any occurrences of new urls from previous list to avoid duplicates.
                    const filteredPrev = prev.filter((u) => !uniqNew.includes(u));

                    const capacity = 9;
                    const combined = [...uniqNew, ...filteredPrev].slice(0, capacity);
                    while (combined.length < capacity) combined.push("");

                    localStorage.setItem(clipsKey, JSON.stringify(combined));

                    // Clip meta (optional): align with slots and mark newest as "generated"
                    const metaKey = `ps_video_clipMeta_v1:${accountKey}`;
                    try {
                      const prevMetaRaw = localStorage.getItem(metaKey) || "[]";
                      const prevMetaArr = JSON.parse(prevMetaRaw);
                      const prevMeta = Array.isArray(prevMetaArr) ? prevMetaArr : [];
                      const prevMetaPaired = prev.map((u, i) => ({ u, m: String(prevMeta[i] || "") }));
                      const filteredMetaPaired = prevMetaPaired.filter((p) => !uniqNew.includes(p.u));
                      const nextMeta = [
                        ...uniqNew.map(() => "generated"),
                        ...filteredMetaPaired.map((p) => p.m),
                      ].slice(0, capacity);
                      while (nextMeta.length < capacity) nextMeta.push("");
                      localStorage.setItem(metaKey, JSON.stringify(nextMeta));
                    } catch {}
                  }
                } catch {
                  // ignore storage failures
                }
              }

              notifiedRef.current.add(vJobId);
              try { localStorage.removeItem(vKey); } catch {}
              const title = act === "merge" ? "Видео объединено" : "Видео готово";
              const msg = vids.length ? "Готово. Итог добавлен в клипы." : "Готово. Перейдите в «Видео».";
              notifyDone(vJobId, {
                studioKey: "video",
                to: "/video",
                count: 1,
                title,
                message: msg,
              });
            } else if (state === "error") {
              const msg = job?.error || "Ошибка видео";
              notifiedRef.current.add(vJobId);
              try { localStorage.removeItem(vKey); } catch {}
              notifyError(vJobId, { studioKey: "video", to: "/video", title: "Ошибка видео" }, msg);
            }
          }
        }
      } catch {
        // ignore transient errors
      } finally {
        timerRef.current = setTimeout(tick, 2000);
      }
    };

    tick();
    return () => stop();
  }, [accountKey, notifyDone, notifyError]);

  return null;
}

export default function ShellLayout(){
  return (
    <AuthProvider>
      <div className="shell">
        <Sidebar/>
        <div className="shellMain">
          <Header/>
          <main className="shellContent"><Outlet/></main>
        </div>
        {/* Global UI overlays */}
        <NotificationCenter />
        <GlobalJobWatcher />
      </div>
    </AuthProvider>
  );
}
