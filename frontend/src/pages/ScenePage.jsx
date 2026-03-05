import React from "react";
import ReactDOM from "react-dom";
import "./ScenePage.css";
import { useAuth } from "../app/AuthContext.jsx";
import { useNavigate, useLocation } from "react-router-dom";
import { STUDIOS } from "./studiosData.js";
import { fetchJson } from "../services/api.js";


function getAccountKey(user){
  return user?.id || "guest";
}

function scJobKey(accountKey, kind){
  return `ps_sc_activeJob_v1:${accountKey}:${String(kind || "").toLowerCase()}`;
}

function scFormatKey(accountKey, kind){
  return `ps_sc_format_v1:${accountKey}:${String(kind || '').toLowerCase()}`;
}

function safeSetLS(key, val){
  try { localStorage.setItem(key, val); } catch {}
}
function safeGetLS(key){
  try { return localStorage.getItem(key); } catch { return null; }
}
function safeRemoveLS(key){
  try { localStorage.removeItem(key); } catch {}
}

function makeMockImageDataUrl({ title = "MOCK", subtitle = "" }) {
  // Simple client-side placeholder (no backend engine yet)
  const w = 1024,
    h = 1024;
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");

  const g = ctx.createLinearGradient(0, 0, w, h);
  g.addColorStop(0, "#1a2a6c");
  g.addColorStop(0.5, "#0b1020");
  g.addColorStop(1, "#b21f1f");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);

  // soft vignette
  const rg = ctx.createRadialGradient(w * 0.5, h * 0.5, 40, w * 0.5, h * 0.5, w * 0.7);
  rg.addColorStop(0, "rgba(255,255,255,0.08)");
  rg.addColorStop(1, "rgba(0,0,0,0.55)");
  ctx.fillStyle = rg;
  ctx.fillRect(0, 0, w, h);

  ctx.fillStyle = "rgba(255,255,255,0.88)";
  ctx.font = "700 64px system-ui, -apple-system, Segoe UI, Roboto, Arial";
  ctx.fillText(title, 72, 120);

  if (subtitle) {
    ctx.fillStyle = "rgba(255,255,255,0.72)";
    ctx.font = "500 34px system-ui, -apple-system, Segoe UI, Roboto, Arial";
    ctx.fillText(subtitle, 72, 170);
  }

  // frame
  ctx.strokeStyle = "rgba(255,255,255,0.18)";
  ctx.lineWidth = 10;
  ctx.strokeRect(40, 40, w - 80, h - 80);

  return c.toDataURL("image/png");
}

export default function ScenePage() {
  const { user, credits, refresh } = useAuth();

  const [activeKind, setActiveKind] = React.useState(null); // "model" | "location"
  const [prompt, setPrompt] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [runMap, setRunMap] = React.useState({ model: null, location: null }); // { jobId, action }

  const [formatMap, setFormatMap] = React.useState({ model: "9:16", location: "9:16" }); // per-kind

  const [status, setStatus] = React.useState("");
  const didHydrateSceneRef = React.useRef(false);
  const didHydrateFormatsRef = React.useRef(false);
  const [didHydrateScene, setDidHydrateScene] = React.useState(false);

  const chatCardRef = React.useRef(null);
  const chatTextareaRef = React.useRef(null);
  const detailsSidebarRef = React.useRef(null);
  const menuRef = React.useRef(null);

  // Portal menu positioning (rendered in document.body to avoid clipping)
  const [menuPortal, setMenuPortal] = React.useState(null); // { tab, key, left, top }

  const nav = useNavigate();
  const loc = useLocation();
  const qs = React.useMemo(() => new URLSearchParams(loc.search || ""), [loc.search]);
  const returnTo = qs.get("returnTo");
const returnModeRaw = qs.get("mode") || qs.get("variant") || qs.get("returnMode") || qs.get("returnVariant");
  const returnMode = React.useMemo(() => {
    const v = (returnModeRaw || "").toString().trim().toUpperCase();
    if (v === "FULL" || v === "FULLBODY" || v === "FULL_BODY") return "FULL";
    if (v === "LEGS" || v === "BOTTOM") return "LEGS";
    if (v === "TORSO" || v === "TOP") return "TORSO";
    return null;
  }, [returnModeRaw]);

  // Hydrate per-panel image format from localStorage (account-scoped).
  React.useEffect(() => {
    const accountKey = getAccountKey(user);
    if (didHydrateFormatsRef.current) return;
    const valid = new Set(["9:16", "1:1", "16:9"]);
    const next = { model: "9:16", location: "9:16" };
    try {
      for (const kind of ["model", "location"]) {
        const raw = safeGetLS(scFormatKey(accountKey, kind));
        const v = (raw || "").toString().trim();
        if (valid.has(v)) next[kind] = v;
      }
    } catch {}
    didHydrateFormatsRef.current = true;
    setFormatMap(next);
  }, [user?.id]);

  const setFormatForKind = React.useCallback((kind, fmt) => {
    const f = (fmt || "").toString().trim();
    if (!["9:16", "1:1", "16:9"].includes(f)) return;
    setFormatMap((prev) => {
      const next = { ...(prev || {}), [kind]: f };
      const accountKey = getAccountKey(user);
      safeSetLS(scFormatKey(accountKey, kind), f);
      return next;
    });
  }, [user]);



  const buildReturnToUrl = React.useCallback(
    (base) => {
      if (!base) return base;
      // If returning to Lookbook, preserve mode so it doesn't reset to TORSO
      const isLookbook = base.startsWith("/studio/lookbook") || base.includes("/studio/lookbook");
      if (!isLookbook) return base;
      if (!returnMode) return base;

      try {
        const u = new URL(base, window.location.origin);
        if (!u.searchParams.get("mode")) u.searchParams.set("mode", returnMode);
        return u.pathname + (u.search ? u.search : "");
      } catch {
        // Fallback for non-standard strings
        const hasQuery = base.includes("?");
        if (base.includes("mode=")) return base;
        return base + (hasQuery ? "&" : "?") + "mode=" + encodeURIComponent(returnMode);
      }
    },
    [returnMode]
  );
  const [studioPickerOpen, setStudioPickerOpen] = React.useState(false);

  const [urlModal, setUrlModal] = React.useState({ open: false, tab: null, key: null, value: "" });

  // Image zoom overlay (model/location preview)
  const [zoomView, setZoomView] = React.useState({ open: false, src: null, alt: "" });
  const openZoom = React.useCallback((src, alt) => {
    if (!src) return;
    setZoomView({ open: true, src, alt: alt || "image" });
  }, []);
  const closeZoom = React.useCallback(() => {
    setZoomView({ open: false, src: null, alt: "" });
  }, []);

  React.useEffect(() => {
    if (!zoomView.open) return;
    const onKey = (e) => {
      if (e.key === "Escape") closeZoom();
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [zoomView.open, closeZoom]);
  const onGoClick = () => {
    if (returnTo) {
      nav(buildReturnToUrl(returnTo));
    } else {
      setStudioPickerOpen(true);
    }
  };

  const [modelImage, setModelImage] = React.useState(null);
  const [locationImage, setLocationImage] = React.useState(null);

  // Per-panel progress overlay (fun phrases while generating / applying details)
  const PROGRESS_PHRASES = React.useMemo(
    () => ({
      model_generate: [
        "Подбираем характер… (и чуть-чуть харизмы)",
        "Выравниваем улыбку по золотому сечению…",
        "Гладим пиксели утюгом… осторожно горячо!",
        "Собираем образ: шляпка ✓, настроение ✓",
        "Проверяем, чтобы глаза смотрели в одну реальность…",
        "Добавляем магию, но без перебора…",
        "Договариваемся с нейросетью вежливо…",
        "Шепчем модели: «будь естественной»…",
      ],
      location_generate: [
        "Расставляем деревья по фэн-шую…",
        "Делаем горизонт ровным. Почти…",
        "Подкрашиваем небо… но не как в 2007-м",
        "Ставим свет так, чтобы было «вау»…",
        "Проверяем, чтобы снег не был пластилином…",
        "Натягиваем атмосферу на каркас реальности…",
        "Уточняем погоду: без сюрпризов, пожалуйста…",
        "Просим солнце не перебивать тени…",
      ],
      model_details: [
        "Пришиваем детали ровной строчкой…",
        "Подгоняем посадку по фигуре…",
        "Сверяем текстуры: «не плывём»…",
        "Добавляем аксессуары, но без перегруза…",
        "Проверяем слои одежды: куртка не телепортируется…",
        "Детали на месте, характер остаётся…",
        "Делаем так, чтобы выглядело как фото, а не сон…",
        "Фиксируем стиль — и не даём ему сбежать…",
      ],
      location_details: [
        "Подмешиваем детали в атмосферу…",
        "Собираем окружение без лишних фантазий…",
        "Сверяем перспективу и размер объектов…",
        "Добавляем акценты: аккуратно и чисто…",
        "Не трогаем фон лишний раз… честное слово…",
        "Шлифуем свет и тени, без мыла…",
        "Проверяем, чтобы пальмы не выросли из снега…",
        "Делаем «красиво и аккуратно» по канону…",
      ],
    }),
    []
  );

  const [panelProgress, setPanelProgress] = React.useState({
    kind: null, // 'model' | 'location'
    phase: null, // 'generate' | 'details'
    phrase: "",
  });
  const progressTimerRef = React.useRef(null);

  const pickProgressPhrase = React.useCallback(
    (kind, phase, prevPhrase) => {
      const key = `${kind}_${phase}`;
      const arr = PROGRESS_PHRASES[key] || [];
      if (!arr.length) return "";
      // avoid repeating same phrase when possible
      if (arr.length === 1) return arr[0];
      for (let i = 0; i < 6; i++) {
        const next = arr[Math.floor(Math.random() * arr.length)];
        if (next !== prevPhrase) return next;
      }
      return arr[0];
    },
    [PROGRESS_PHRASES]
  );

  const startPanelProgress = React.useCallback(
    (kind, phase) => {
      setPanelProgress({
        kind,
        phase,
        phrase: pickProgressPhrase(kind, phase, ""),
      });
    },
    [pickProgressPhrase]
  );

  const stopPanelProgress = React.useCallback(() => {
    setPanelProgress({ kind: null, phase: null, phrase: "" });
  }, []);



// Restore running scene jobs after navigation / refresh (so progress continues)
React.useEffect(() => {
  const accountKey = getAccountKey(user);
  const next = { model: null, location: null };

  for (const kind of ["model", "location"]) {
    const k = scJobKey(accountKey, kind);
    const raw = safeGetLS(k);
    if (!raw) continue;
    try {
      const rec = JSON.parse(raw);
      if (rec?.jobId) next[kind] = { jobId: String(rec.jobId), action: rec?.action || "" };
    } catch {}
  }
  setRunMap(next);

  // If a job was already running before navigation/refresh, restore the same progress overlay.
  // Prefer model, then location (only one overlay at a time, keeps UI clean).
  try {
    if (!panelProgress.kind) {
      const firstKind = next?.model?.jobId ? "model" : next?.location?.jobId ? "location" : null;
      if (firstKind) {
        const action = String(next?.[firstKind]?.action || "");
        startPanelProgress(firstKind, action === "applyDetails" ? "details" : "generate");
        setStatus("Восстановили процесс… генерация продолжается.");
      }
    }
  } catch {}

}, [user?.id]);

// Poll running jobs while user is on ScenePage (so UI updates immediately on completion)
React.useEffect(() => {
  let cancelled = false;
  const accountKey = getAccountKey(user);

  const tick = async () => {
    try {
      for (const kind of ["model", "location"]) {
        const rec = runMap?.[kind];
        if (!rec?.jobId) continue;

        const res = await fetchJson(`/api/scene/jobs/${rec.jobId}`);
        const job = res?.job;
        const state = job?.state;

        if (state === "done") {
          safeRemoveLS(scJobKey(accountKey, kind));
          if (!cancelled) {
            setRunMap((prev) => ({ ...(prev || {}), [kind]: null }));
            try {
              const cur = await fetchJson("/api/scene/current");
              const sc = cur?.scene || null;
              if (sc) {
                if (kind === "model") setModelImage(sc.modelUrl || null);
                if (kind === "location") setLocationImage(sc.locationUrl || null);
              }
            } catch {}
            // stop overlay if it belongs to this panel
            setPanelProgress((p) => {
              if (!p) return p;
              if (p.kind === kind) return { kind: null, phase: null, phrase: "" };
              return p;
            });
            setBusy(false);
            setStatus("Готово ✅");
          }
        }

        if (state === "error") {
          safeRemoveLS(scJobKey(accountKey, kind));
          if (!cancelled) {
            setRunMap((prev) => ({ ...(prev || {}), [kind]: null }));
            setPanelProgress((p) => {
              if (!p) return p;
              if (p.kind === kind) return { kind: null, phase: null, phrase: "" };
              return p;
            });
            setBusy(false);
          }
        }
      }
    } catch {
      // ignore transient
    } finally {
      if (!cancelled) setTimeout(tick, 1500);
    }
  };

  tick();
  return () => { cancelled = true; };
}, [user?.id, runMap]);

  React.useEffect(() => {
    // randomized 3–5s ticker
    if (progressTimerRef.current) {
      clearTimeout(progressTimerRef.current);
      progressTimerRef.current = null;
    }
    if (!panelProgress.kind || !panelProgress.phase) return;

    let alive = true;
    const tick = () => {
      if (!alive) return;
      const ms = 3000 + Math.floor(Math.random() * 2001); // 3000..5000
      progressTimerRef.current = setTimeout(() => {
        if (!alive) return;
        setPanelProgress((prev) => {
          if (!prev.kind || !prev.phase) return prev;
          return {
            ...prev,
            phrase: pickProgressPhrase(prev.kind, prev.phase, prev.phrase),
          };
        });
        tick();
      }, ms);
    };
    tick();
    return () => {
      alive = false;
      if (progressTimerRef.current) {
        clearTimeout(progressTimerRef.current);
        progressTimerRef.current = null;
      }
    };
  }, [panelProgress.kind, panelProgress.phase, pickProgressPhrase]);

  // Backward-compatible aliases (some patches referenced these names)
  const modelResult = modelImage;
  const locationResult = locationImage;


  async function persistDataUrlToAssetUrl(dataUrl) {
    const res = await fetchJson("/api/assets/fromDataUrl", { method: "POST", body: { dataUrl } });
    return res?.url || null;
  }

  async function patchScene(patch) {
    if (!didHydrateSceneRef.current) return;
    try {
      await fetchJson("/api/scene/current", { method: "PATCH", body: patch });
    } catch (e) {
      console.warn("[scene] patch failed:", e?.message || e);
    }
  }

  // Hydrate scene draft from server (account-scoped via auth cookie)
  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetchJson("/api/scene/current");
        const sc = res?.scene || {};
        if (!alive) return;
        setModelImage(sc.modelUrl || null);
        setLocationImage(sc.locationUrl || null);
        setModelDetailSlots({
          head: sc?.modelDetails?.head || null,
          torso: sc?.modelDetails?.torso || null,
          legs: sc?.modelDetails?.legs || null,
          shoes: sc?.modelDetails?.shoes || null,
          accessory: sc?.modelDetails?.accessory || null,
        });
        const locArr = Array.isArray(sc?.locationDetails)
          ? sc.locationDetails
          : Array(5).fill(null).map((_, i) => sc?.locationDetails?.[i] || null);
        setLocationDetailSlots(Array(5).fill(null).map((_, i) => locArr[i] || null));
      } catch (e) {
        console.warn("[scene] hydrate failed:", e?.message || e);
      } finally {
        if (!alive) return;
        didHydrateSceneRef.current = true;
        setDidHydrateScene(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [user?.id]);

  // Details (v1 UI): slide-out sidebar with per-slot mini menu.
  // Active only when base image exists (model/location).
  const MODEL_GROUPS = React.useMemo(
    () => [
      { key: "head", top: "Головной", bottom: "убор" },
      { key: "torso", top: "Верхняя", bottom: "одежда" },
      { key: "legs", top: "Нижняя", bottom: "одежда" },
      { key: "shoes", top: "Обувь", bottom: "" },
      { key: "accessory", top: "Аксессуары", bottom: "" },
    ],
    []
  );

  const LOCATION_GROUPS = React.useMemo(
    () => [
      { key: 0, top: "Деталь", bottom: "1" },
      { key: 1, top: "Деталь", bottom: "2" },
      { key: 2, top: "Деталь", bottom: "3" },
      { key: 3, top: "Деталь", bottom: "4" },
      { key: 4, top: "Деталь", bottom: "5" },
    ],
    []
  );

  // null | "model" | "location"
  const [detailsOpen, setDetailsOpen] = React.useState(null);

  const [modelDetailSlots, setModelDetailSlots] = React.useState(() => ({
    head: null,
    torso: null,
    legs: null,
    shoes: null,
    accessory: null,
  }));
  const [locationDetailSlots, setLocationDetailSlots] = React.useState(() => Array(5).fill(null));

  // Draft while sidebar is open (Apply/Cancel)
  const [draftModelDetailSlots, setDraftModelDetailSlots] = React.useState(null);
  const [draftLocationDetailSlots, setDraftLocationDetailSlots] = React.useState(null);

  // Slot mini-menu
  const [openMenuKey, setOpenMenuKey] = React.useState(null); // e.g. "model:head" or "location:2"
  const [menuPlacement, setMenuPlacement] = React.useState(() => ({})); // { [openMenuKey]: 'up'|'down' }

  // Hidden file input for slot uploads
  const fileInputRef = React.useRef(null);
  const [pendingSlotTarget, setPendingSlotTarget] = React.useState(null); // { tab, key }

  const isDetailsLocked = !!detailsOpen;

  const canOpenModelDetails = !!modelResult;
  const canOpenLocationDetails = !!locationResult;

  const openChat = (kind) => {
    setActiveKind(kind);
    setStatus(kind === "model" ? "Выбран чат: Модель" : "Выбран чат: Локация");

    // Make chat panel visually active and bring it into view + focus textarea
    window.requestAnimationFrame(() => {
      try {
        chatCardRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
      } catch {}
      try {
        chatTextareaRef.current?.focus();
      } catch {}
    });
  };

  const onCreate = async () => {
    if (!activeKind) {
      setStatus("Сначала выберите: под Моделью или Локацией нажмите «Создать».");
      return;
    }
    const p = prompt.trim();
    if (!p) {
      setStatus("Введите текст для генерации.");
      return;
    }
    if (busy) return;

    let launchedJob = false;

    setBusy(true);
    startPanelProgress(activeKind, "generate");
    setStatus("Запуск генерации…");
    try {
      setStatus("Генерация…");
      const res = await fetchJson("/api/scene/generateJob", {
        method: "POST",
        body: { kind: activeKind, prompt: p, format: (formatMap?.[activeKind] || "9:16") },
      });

      const jobId = res?.jobId || null;
      if (!jobId) {
        setStatus("Ошибка: сервер не вернул jobId.");
        return;
      }

      // Persist active job so generation continues after leaving page
      const accountKey = getAccountKey(user);
      safeSetLS(scJobKey(accountKey, activeKind), JSON.stringify({ jobId, action: "generate" }));
      setRunMap((prev) => ({ ...(prev || {}), [activeKind]: { jobId: String(jobId), action: "generate" } }));

      launchedJob = true;

      // IMPORTANT: do NOT wait for completion here — user can navigate away.
      setStatus("Генерация запущена… Можно уходить со страницы, результат придёт позже.");
      setBusy(false);
      return;

      if (activeKind === "model") {
        setModelImage(assetUrl);
        await patchScene({ modelUrl: assetUrl });
      } else {
        setLocationImage(assetUrl);
        await patchScene({ locationUrl: assetUrl });
      }

      setStatus("Готово. Теперь можно «Сохранить» или добавить детали.");
      setPrompt("");
      await refresh();
    } catch (e) {
      console.error(e);
      const msg = e && e.message ? String(e.message) : "";
      setStatus(`Ошибка генерации. Проверь backend /api. ${msg}`.trim());
    } finally {
      setBusy(false);
      if (!launchedJob) stopPanelProgress();
    }
  };


  const onSave = () => {
    // v0: only local page state; later: server-side scene storage
    setStatus("Сохранение будет подключено позже (v1). Сейчас это черновик UI.");
  };

  const closeDetails = () => {
    setDetailsOpen(null);
    setOpenMenuKey(null);
    setPendingSlotTarget(null);
  };

  const onOpenDetails = (kind) => {
    // Only allow details when the base image exists
    if (kind === "model" && !canOpenModelDetails) {
      setStatus("Сначала создайте модель.");
      return;
    }
    if (kind === "location" && !canOpenLocationDetails) {
      setStatus("Сначала создайте локацию.");
      return;
    }

    setDraftModelDetailSlots({ ...modelDetailSlots });
    setDraftLocationDetailSlots([...locationDetailSlots]);
    setDetailsOpen(kind);
    setOpenMenuKey(null);
  };

  const applyDetails = async () => {
    if (busy) return;

    if (!detailsOpen) return;

    let launchedJob = false;

    // Apply details costs 1 credit
    setBusy(true);
    startPanelProgress(detailsOpen, "details");
    setStatus("Применяем детали…");
    try {
// 1) persist detail slots into scene draft
      let detailUrls = [];
      if (draftModelDetailSlots && detailsOpen === "model") {
        const next = { ...draftModelDetailSlots };
        setModelDetailSlots(next);
        await patchScene({ modelDetails: next });
        detailUrls = Object.values(next).filter(Boolean);
      }
      if (draftLocationDetailSlots && detailsOpen === "location") {
        const next = [...draftLocationDetailSlots];
        setLocationDetailSlots(next);
        await patchScene({ locationDetails: next });
        detailUrls = next.filter(Boolean);
      }

      if (!detailUrls.length) {
        setStatus("Нет деталей для применения.");
        closeDetails();
        await refresh();
        return;
      }

      // 2) engine apply (regenerate base image with detail refs)
      setStatus("Применяем детали…");
      const baseUrl = detailsOpen === "model" ? modelResult : locationResult;
      const res = await fetchJson("/api/scene/applyDetailsJob", {
        method: "POST",
        body: {
          kind: detailsOpen,
          baseUrl,
          detailUrls,
          format: (formatMap?.[detailsOpen] || "9:16"),
        },
      });

      const jobId = res?.jobId || null;
      if (!jobId) {
        setStatus("Ошибка: сервер не вернул jobId.");
        return;
      }

      const accountKey = getAccountKey(user);
      safeSetLS(scJobKey(accountKey, detailsOpen), JSON.stringify({ jobId, action: "applyDetails" }));
      setRunMap((prev) => ({ ...(prev || {}), [detailsOpen]: { jobId: String(jobId), action: "applyDetails" } }));

      launchedJob = true;

      setStatus("Применяем детали… Можно уходить со страницы, результат придёт позже.");
      setBusy(false);
      return;

      if (detailsOpen === "model") {
        setModelImage(newUrl);
        await patchScene({ modelUrl: newUrl });
      } else {
        setLocationImage(newUrl);
        await patchScene({ locationUrl: newUrl });
      }

      setStatus(detailsOpen === "model" ? "Детали модели применены." : "Детали локации применены.");
      closeDetails();
      await refresh();
    } catch (e) {
      console.error(e);
      setStatus("Ошибка: не удалось применить детали.");
    } finally {
      setBusy(false);
      if (!launchedJob) stopPanelProgress();
    }
  };

  const renderPanelProgressOverlay = (kind) => {
    if (!panelProgress.kind || panelProgress.kind !== kind) return null;
    const title = panelProgress.phase === "details" ? "Применяем детали…" : "Генерация…";
    return (
      <div className={`sp-progressOverlay ${kind === "location" ? "isGreen" : "isBlue"}`}>
        <div className="sp-progressInner">
          <div className="sp-progressSpinner" aria-hidden="true" />
          <div className="sp-progressTitle">{title}</div>
          <div className="sp-progressPhrase">{panelProgress.phrase || "…"}</div>
          <div className="sp-progressDots" aria-hidden="true">
            <span>•</span>
            <span>•</span>
            <span>•</span>
          </div>
        </div>
      </div>
    );
  };
  const cancelDetails = () => {
    setStatus("Отмена деталей.");
    closeDetails();
  };

  const setDraftSlotValue = (tab, key, value) => {
    if (tab === "model") {
      setDraftModelDetailSlots((prev) => ({ ...(prev || {}), [key]: value }));
    } else {
      setDraftLocationDetailSlots((prev) => {
        const next = Array.isArray(prev) ? [...prev] : Array(5).fill(null);
        next[key] = value;
        return next;
      });
    }
  };

  const readFileAsDataUrl = (file) =>
    new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onerror = () => reject(new Error("FileReader error"));
      r.onload = () => resolve(String(r.result || ""));
      r.readAsDataURL(file);
    });

  const handlePickFile = async (tab, key, file) => {
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const assetUrl = await persistDataUrlToAssetUrl(dataUrl);
      setDraftSlotValue(tab, key, assetUrl);
    } catch (e) {
      console.error(e);
      setStatus("Ошибка: не удалось прочитать файл.");
    }
  };

  const requestFileForSlot = (tab, key) => {
    setPendingSlotTarget({ tab, key });
    setOpenMenuKey(null);
    setMenuPortal(null);
    fileInputRef.current?.click();
  };

  const requestUrlForSlot = (tab, key) => {
    const cur =
      tab === "model" ? draftModelDetailSlots?.[key] || "" : draftLocationDetailSlots?.[key] || "";
    setUrlModal({ open: true, tab, key, value: cur || "" });
    setOpenMenuKey(null);
    setMenuPortal(null);
  };
  const closeUrlModal = () => setUrlModal({ open: false, tab: null, key: null, value: "" });
  const confirmUrlModal = () => {
    if (!urlModal.open) return;
    const v = (urlModal.value || "").trim();
    if (!v) {
      closeUrlModal();
      return;
    }
    setDraftSlotValue(urlModal.tab, urlModal.key, v);
    closeUrlModal();
  };

  const deleteSlot = (tab, key) => {
    setDraftSlotValue(tab, key, null);
    setOpenMenuKey(null);
    setMenuPortal(null);
  };

  const toggleSlotMenu = (tab, key, slotEl) => {
    const mk = `${tab}:${String(key)}`;
    setOpenMenuKey((prev) => {
      const next = prev === mk ? null : mk;
      if (next && slotEl) {
        // If slot is close to bottom edge, open the menu upwards
        const rect = slotEl.getBoundingClientRect();
        const viewportH = window.innerHeight || 800;
        const spaceBelow = viewportH - rect.bottom;
        const spaceAbove = rect.top;
        // rough menu height (4 buttons + padding)
        const need = 240;
        const place = spaceBelow < need && spaceAbove > spaceBelow ? "up" : "down";
        setMenuPlacement((mp) => ({ ...(mp || {}), [mk]: place }));

        // Compute portal coordinates in viewport
        const menuW = 210;
        const pad = 10;
        let left = rect.right + 10;
        if (left + menuW + pad > (window.innerWidth || 1200)) {
          left = Math.max(pad, rect.left - menuW - 10);
        }
        let top = rect.top;
        if (place === "up") {
          top = Math.max(pad, rect.bottom - need);
        }
        setMenuPortal({ tab, key, left, top });
      }
      if (!next) setMenuPortal(null);
      return next;
    });
  };

  // ✅ ЕДИНСТВЕННЫЙ clearKind (без дублей и без мусора)
  const clearKind = async (kind) => {
    if (kind === "model") {
      setModelImage(null);
      setModelDetailSlots({ head: null, torso: null, legs: null, shoes: null, accessory: null });
      await patchScene({ modelUrl: null, modelDetails: { head: null, torso: null, legs: null, shoes: null, accessory: null } });
    } else {
      setLocationImage(null);
      setLocationDetailSlots(Array(5).fill(null));
      await patchScene({ locationUrl: null, locationDetails: Array(5).fill(null) });
    }
    setStatus("Очищено.");
  };
  // Details sidebar (as separate grid column; NOT inside cards)
  const renderDetailsSidebar = () => {
    if (!detailsOpen) return null;

    const isModel = detailsOpen === "model";
    const title = isModel ? "+ детали (модель)" : "+ детали (локация)";
    const groups = isModel ? MODEL_GROUPS : LOCATION_GROUPS;
    const tab = isModel ? "model" : "location";

    return (
      <section className="sp-detailsSidebar" ref={detailsSidebarRef}>
        <div className="sp-detailsHead">
          <div className="sp-detailsTitle">{title}</div>
          <button className="sp-iconBtn" type="button" onClick={cancelDetails}>
            ×
          </button>
        </div>

        <div className="sp-detailsSlots">
          {groups.map((g) => {
            const key = g.key;
            const v = isModel
              ? draftModelDetailSlots?.[key] || null
              : draftLocationDetailSlots?.[key] || null;
            const mk = `${tab}:${String(key)}`;
            const menuOpen = openMenuKey === mk;

            return (
              <div key={String(key)} className="sp-detailsSlot" onClick={(e) => toggleSlotMenu(tab, key, e.currentTarget)}>
                <div className="sp-detailsSlotLabel sp-detailsSlotLabelTop">{g.top}</div>

                <div className="sp-detailsSlotPreview">
                  {v ? <img src={v} alt={String((g.top + " " + (g.bottom||"")).trim())} /> : <div className="sp-detailsSlotPlus">+</div>}
                </div>

                {g.bottom ? (<div className="sp-detailsSlotLabel sp-detailsSlotLabelBottom">{g.bottom}</div>) : null}

                {/* меню рендерим в portal поверх всего (см. renderSlotMenuPortal) */}
                {menuOpen ? null : null}
              </div>
            );
          })}
        </div>

        <div className="sp-detailsActions">
          <button
            className={"sp-btn sp-btnPrimary" + (detailsOpen === "location" ? " sp-btnGreen" : "")}
            type="button"
            onClick={applyDetails}
            disabled={busy || (detailsOpen && runMap?.[detailsOpen])}
          >
            {busy ? "применяем…" : "применить"}
          </button>
        </div>
      </section>
    );
  };

  const renderSlotMenuPortal = () => {
    if (!detailsOpen) return null;
    if (!openMenuKey || !menuPortal) return null;

    const tab = menuPortal.tab;
    const key = menuPortal.key;
    const v = tab === "model" ? draftModelDetailSlots?.[key] || null : draftLocationDetailSlots?.[key] || null;
    const style = { left: Math.round(menuPortal.left), top: Math.round(menuPortal.top) };

    return ReactDOM.createPortal(
      <div className="sp-slotMenuPortal" style={style} ref={menuRef}>
        <button type="button" onClick={() => requestFileForSlot(tab, key)}>
          С компа
        </button>
        <button type="button" onClick={() => requestUrlForSlot(tab, key)}>
          URL
        </button>
        <button type="button" onClick={() => deleteSlot(tab, key)} disabled={!v}>
          Удалить
        </button>
        <div className="sp-slotMenuSep" />
        <button
          type="button"
          onClick={() => {
            setOpenMenuKey(null);
            setMenuPortal(null);
          }}
        >
          Отмена
        </button>
      </div>,
      document.body
    );
  };

  const renderUrlModalPortal = () => {
    if (!urlModal?.open) return null;
    return ReactDOM.createPortal(
      <div
        className="sp-urlModalOverlay"
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) closeUrlModal();
        }}
      >
        <div className="sp-urlModal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
          <div className="sp-urlModalHead">
            <div className="sp-urlModalTitle">Вставь URL картинки</div>
            <button className="sp-iconBtn" type="button" onClick={closeUrlModal}>
              ×
            </button>
          </div>
          <input
            className="sp-urlModalInput"
            value={urlModal.value}
            onChange={(e) => setUrlModal((s) => ({ ...s, value: e.target.value }))}
            placeholder="https://... или data:image/..."
            autoFocus
          />
          <div className="sp-urlModalActions">
            <button className="sp-btn sp-btnGhost" type="button" onClick={closeUrlModal}>
              отмена
            </button>
            <button className="sp-btn sp-btnPrimary" type="button" onClick={confirmUrlModal}>
              ok
            </button>
          </div>
        </div>
      </div>,
      document.body
    );
  };

  React.useEffect(() => {
    if (!detailsOpen) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        if (urlModal?.open) {
          closeUrlModal();
          return;
        }
        setDetailsOpen(null);
        setOpenMenuKey(null);
        setMenuPortal(null);
        setPendingSlotTarget(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detailsOpen, urlModal?.open]);

  // Close slot menu on click anywhere (keep open only when clicking inside the menu)
  React.useEffect(() => {
    if (!detailsOpen) return;
    const onDown = (e) => {
      if (!openMenuKey) return;
      const menuEl = menuRef.current;
      if (menuEl && menuEl.contains(e.target)) return;
      setOpenMenuKey(null);
      setMenuPortal(null);
    };
    window.addEventListener("mousedown", onDown, true);
    window.addEventListener("touchstart", onDown, true);
    return () => {
      window.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("touchstart", onDown, true);
    };
  }, [detailsOpen, openMenuKey]);

  return (
    <div className="sp-wrap">
      {renderSlotMenuPortal()}
      {renderUrlModalPortal()}
      <div className="sp-titleBlock">
        <div className="sp-titleRow">
          <div>
            <div className="sp-title">Создание сцены и образа</div>
            <div className="sp-sub">Создай свою модель и локацию. Пока это UI v0 + mock-списание кредитов.</div>
          </div>
          <button className="sp-goBtn" type="button" onClick={onGoClick}>
            {returnTo ? "вернуться" : "перейти"}
          </button>
        </div>
      </div>

      <div
        className={
          "sp-grid" +
          (detailsOpen === "model"
            ? " hasDetailsModel isDetailsLocked"
            : detailsOpen === "location"
            ? " hasDetailsLocation isDetailsLocked"
            : "")
        }
      >
        <section className="sp-card sp-cardModel">
          <div className="sp-cardHead">
            <div className="sp-cardName">модель</div>
            <div className="sp-cardActions">
              <button className="sp-iconBtn" type="button" title="Очистить" onClick={() => clearKind("model")}>
                ×
              </button>
              <button
                className={"sp-pillBtn" + (detailsOpen === "model" ? " isActive" : "")}
                type="button"
                onClick={() => onOpenDetails("model")}
                disabled={!canOpenModelDetails}
              >
                + детали
              </button>
            </div>
          </div>

          <div className="sp-preview">
            {modelImage ? (
              <>
                <img
                  className="sp-img sp-imgClickable"
                  src={modelImage}
                  alt="model"
                  onClick={() => openZoom(modelImage, "model")}
                />
                {renderPanelProgressOverlay("model")}
              </>
            ) : (
              <>
                <div className="sp-empty">Модель</div>
                {renderPanelProgressOverlay("model")}
              </>
            )}
          </div>

          <div className="sp-bottomBar">
            <button className="sp-btn sp-btnPrimary" type="button" onClick={() => openChat("model")}>
              создать
            </button>
            <button className="sp-btn sp-btnGhost" type="button" onClick={onSave}>
              сохранить
            </button>
          </div>
        </section>

        {detailsOpen === "model" ? renderDetailsSidebar() : null}

        <section ref={chatCardRef} className={"sp-card sp-cardChat" + (activeKind ? ` is-${activeKind}` : "") + (activeKind ? " isActive" : "")}>
          <div className="sp-cardHead">
            <div className="sp-cardName">чат</div>
            <button
              className="sp-iconBtn"
              type="button"
              title="Сброс"
              onClick={() => {
                setActiveKind(null);
                setStatus("Чат сброшен.");
              }}
            >
              ×
            </button>
          </div>

          <div className="sp-chatHint">
            Выберите «создать» под <b>Моделью</b> или <b>Локацией</b>.
          </div>

          <textarea
            ref={chatTextareaRef}
            className="sp-textarea"
            placeholder={
              activeKind
                ? activeKind === "model"
                  ? "Опиши модель (внешность, стиль, возраст…)"
                  : "Опиши локацию (место, время суток, атмосфера…)"
                : "Сначала выберите Модель или Локацию"
            }
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            disabled={!activeKind || busy}
          />

          <div className="sp-chatActions">
            <button className={"sp-btn sp-btnPrimary sp-chatCreateBtn " + (activeKind === "model" ? "isModel" : activeKind === "location" ? "isLocation" : "")} type="button" onClick={onCreate} disabled={!activeKind || busy}>
              {busy ? "..." : "создать"}
            </button>
          </div>

          <div className="sp-statusCard">
            <div className="sp-statusTitle">статус</div>
            <div className="sp-statusText">{status || "Пока пусто. Сделайте первую генерацию."}</div>
            <div className="sp-statusMeta">
              {user ? (
                <>
                  <span>
                    Пользователь: <b>{user.name || user.email}</b>
                  </span>
                  <span>
                    Кредиты: <b>{credits}</b>
                  </span>
                </>
              ) : (
                <span>Гость</span>
              )}
            </div>
          </div>

          <div className={"sp-formatBar " + (activeKind === "model" ? "isModel" : activeKind === "location" ? "isLocation" : "")}>
            <div className="sp-formatLabel">формат</div>
            <div className="sp-formatPills" role="group" aria-label="Формат">
              {["9:16", "1:1", "16:9"].map((f) => {
                const kind = activeKind || "model";
                const cur = formatMap?.[kind] || "9:16";
                const isOn = cur === f;
                return (
                  <button
                    key={f}
                    type="button"
                    className={"sp-formatPill " + (isOn ? "isOn" : "")}
                    onClick={() => setFormatForKind(kind, f)}
                    disabled={busy}
                    title={f === "9:16" ? "Вертикаль" : f === "1:1" ? "Квадрат" : "Горизонталь"}
                  >
                    {f}
                  </button>
                );
              })}
            </div>
            <div className="sp-formatHint">{activeKind ? (activeKind === "model" ? "Модель" : "Локация") : "Выберите Модель или Локацию"}</div>
          </div>
        </section>

        {detailsOpen === "location" ? renderDetailsSidebar() : null}

        <section className="sp-card sp-cardLoc">
          <div className="sp-cardHead">
            <div className="sp-cardName">локация</div>
            <div className="sp-cardActions">
              <button className="sp-iconBtn" type="button" title="Очистить" onClick={() => clearKind("location")}>
                ×
              </button>
              <button
                className={"sp-pillBtn sp-pillGreen" + (detailsOpen === "location" ? " isActive" : "")}
                type="button"
                onClick={() => onOpenDetails("location")}
                disabled={!canOpenLocationDetails}
              >
                + детали
              </button>
            </div>
          </div>

          <div className="sp-preview">
            {locationImage ? (
              <>
                <img
                  className="sp-img sp-imgClickable"
                  src={locationImage}
                  alt="location"
                  onClick={() => openZoom(locationImage, "location")}
                />
                {renderPanelProgressOverlay("location")}
              </>
            ) : (
              <>
                <div className="sp-empty">Локация</div>
                {renderPanelProgressOverlay("location")}
              </>
            )}
          </div>

          <div className="sp-bottomBar">
            <button className="sp-btn sp-btnPrimary sp-btnGreen" type="button" onClick={() => openChat("location")}>
              создать
            </button>
            <button className="sp-btn sp-btnGhost" type="button" onClick={onSave}>
              сохранить
            </button>
          </div>
        </section>
      </div>

      
      {zoomView.open
        ? ReactDOM.createPortal(
            <div
              className="sp-zoomOverlay"
              role="dialog"
              aria-modal="true"
              onMouseDown={closeZoom}
              onClick={closeZoom}
            >
              <img className="sp-zoomImg" src={zoomView.src} alt={zoomView.alt || "zoom"} />
            </div>,
            document.body
          )
        : null}

{/* Hidden file input for details slot uploads */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files && e.target.files[0];
          if (!f || !pendingSlotTarget) return;
          const { tab, key } = pendingSlotTarget;
          e.target.value = "";
          handlePickFile(tab, key, f);
          setPendingSlotTarget(null);
        }}
      />
      {studioPickerOpen && (
        <div className="sp-studioPickerBackdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setStudioPickerOpen(false); }}>
          <div className="sp-studioPicker">
            <div className="sp-studioPickerHead">
              <div className="sp-studioPickerTitle">Выберите студию</div>
              <button className="sp-studioPickerClose" type="button" onClick={() => setStudioPickerOpen(false)}>×</button>
            </div>
            <div className="sp-studioPickerSub">Выберите, куда перейти. Данные сцены сохранятся.</div>
            <div className="sp-studioPickerGrid">
              {STUDIOS.slice(0, 10).map((st) => (
                <button
                  key={st.key}
                  type="button"
                  className="sp-studioCard"
                  onClick={() => {
                    setStudioPickerOpen(false);
                    nav(`/studio/${st.key}`);
                  }}
                >
                  <div className={"sp-studioCardMedia accent-" + (st.accent || "cyan")}>
                    <div className="sp-studioCardLetter">{st.letter || "•"}</div>
                  </div>
                  <div className="sp-studioCardBody">
                    <div className="sp-studioCardTitle">{st.title}</div>
                    <div className="sp-studioCardDesc">{st.desc}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {urlModal.open && (
        <div className="sp-urlBackdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) closeUrlModal(); }}>
          <div className="sp-urlModal">
            <div className="sp-urlHead">
              <div className="sp-urlTitle">Вставь URL картинки</div>
              <button className="sp-urlClose" type="button" onClick={closeUrlModal}>×</button>
            </div>
            <input
              className="sp-urlInput"
              value={urlModal.value}
              onChange={(e) => setUrlModal((m) => ({ ...m, value: e.target.value }))}
              placeholder="https://... или data:image/..."
              autoFocus
            />
            <div className="sp-urlActions">
              <button className="sp-urlBtn" type="button" onClick={closeUrlModal}>отмена</button>
              <button className="sp-urlBtn sp-urlBtnOk" type="button" onClick={confirmUrlModal}>ok</button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}