import React from "react";
import ReactDOM from "react-dom";
import { fetchJson, API_BASE } from "../services/api.js";
import { useAuth } from "../app/AuthContext.jsx";
import "./PrintsPage.css";

// hydrate double-mount guard (dev StrictMode) — skip only if called again within a very short window
function clamp(n, a, b) {
  return Math.max(a, Math.min(b, n));
}

function computeFitFromWarp(warp){
  try{
    const top = clamp(parseFloat(warp?.top ?? 0) || 0, -1, 1);
    const right = clamp(parseFloat(warp?.right ?? 0) || 0, -1, 1);
    const bottom = clamp(parseFloat(warp?.bottom ?? 0) || 0, -1, 1);
    const left = clamp(parseFloat(warp?.left ?? 0) || 0, -1, 1);
    const curveV = clamp(bottom - top, -1, 1);
    const curveH = clamp(right - left, -1, 1);
    const needV = Math.abs(curveV) * 0.28;
    const needH = Math.abs(curveH) * 0.22;
    const baseInset = 0.04;
    const inset = clamp(baseInset + Math.max(needV, needH), 0, 0.35);
    const fitScale = clamp(1 - inset * 2, 0.1, 1);
    return { inset, fitScale };
  }catch(e){
    return { inset: 0.04, fitScale: 0.92 };
  }
}

function rotToDelta(deg){
  if (deg == null) return 0;
  const d = ((deg % 360) + 360) % 360;
  return d <= 180 ? d : d - 360;
}
function deltaToRot(delta){
  const d = parseInt(delta, 10) || 0;
  return ((d % 360) + 360) % 360;
}

function resolveUrl(u) {
  if (!u) return "";
  if (/^https?:\/\//i.test(u)) return u;
  if (u.startsWith("/")) return `${API_BASE}${u}`;
  return u;
}

// --- Pin preview: remove background from logo (preview-only) -----------------
function _clamp01(x){ return Math.max(0, Math.min(1, x)); }

function _loadImg(src){
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = resolveUrl(src);
  });
}

function _computeBgColorFromBorder(data, w, h){
  // Average border pixels (ignore fully transparent)
  let r=0,g=0,b=0,c=0;
  const take = (x,y)=>{
    const i=(y*w+x)*4;
    const a=data[i+3];
    if (a < 10) return;
    r += data[i]; g += data[i+1]; b += data[i+2]; c++;
  };
  for (let x=0;x<w;x++){ take(x,0); take(x,h-1); }
  for (let y=1;y<h-1;y++){ take(0,y); take(w-1,y); }
  if (!c) return {r:255,g:255,b:255};
  return {r:r/c, g:g/c, b:b/c};
}

function _bgThresholdFromBorder(data,w,h,bg){
  // Measure border noise to set adaptive threshold
  let sum=0, sum2=0, c=0;
  const dist = (i)=>{
    const dr=data[i]-bg.r, dg=data[i+1]-bg.g, db=data[i+2]-bg.b;
    return Math.sqrt(dr*dr+dg*dg+db*db);
  };
  const take=(x,y)=>{
    const i=(y*w+x)*4;
    const a=data[i+3];
    if (a < 10) return;
    const d=dist(i);
    sum += d; sum2 += d*d; c++;
  };
  for (let x=0;x<w;x++){ take(x,0); take(x,h-1); }
  for (let y=1;y<h-1;y++){ take(0,y); take(w-1,y); }
  if (!c) return 28;
  const mean = sum/c;
  const varr = Math.max(0, (sum2/c) - mean*mean);
  const std = Math.sqrt(varr);
  // base + 3σ, clamped
  const t = mean + std*3 + 6;
  return Math.max(18, Math.min(70, t));
}

function _dilateMask(mask,w,h,iter){
  // mask is Uint8Array 0/1
  let src = mask;
  for (let k=0;k<iter;k++){
    const dst = new Uint8Array(src.length);
    for (let y=0;y<h;y++){
      for (let x=0;x<w;x++){
        const idx=y*w+x;
        if (src[idx]) { dst[idx]=1; continue; }
        let on=0;
        for (let yy=Math.max(0,y-1); yy<=Math.min(h-1,y+1); yy++){
          const row=yy*w;
          for (let xx=Math.max(0,x-1); xx<=Math.min(w-1,x+1); xx++){
            if (src[row+xx]) { on=1; break; }
          }
          if (on) break;
        }
        if (on) dst[idx]=1;
      }
    }
    src = dst;
  }
  return src;
}

function _removeBgPreview(img){
  // If the image already has alpha (PNG with transparency), don't fight it.
  // We'll still run the algorithm, but it will mostly keep existing alpha.
  const maxSide = 420;
  const scale = Math.min(1, maxSide / Math.max(img.naturalWidth||1, img.naturalHeight||1));
  const w = Math.max(1, Math.round((img.naturalWidth||1)*scale));
  const h = Math.max(1, Math.round((img.naturalHeight||1)*scale));

  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.clearRect(0,0,w,h);
  ctx.drawImage(img, 0, 0, w, h);

  const im = ctx.getImageData(0,0,w,h);
  const d = im.data;

  // If the source already contains meaningful transparency (PNG with alpha), keep it as-is.
  // Our background-removal heuristic can mistakenly erase such images.
  let alphaHits = 0;
  for (let i=3; i<d.length; i+=4) {
    const a = d[i];
    if (a > 0 && a < 250) { alphaHits++; if (alphaHits > (w*h*0.01)) break; }
  }
  if (alphaHits > (w*h*0.01)) {
    return c;
  }

  const bg = _computeBgColorFromBorder(d,w,h);
  const thr = _bgThresholdFromBorder(d,w,h,bg);

  // Build foreground mask by color distance from border background (adaptive)
  const mask = new Uint8Array(w*h);
  for (let y=0;y<h;y++){
    for (let x=0;x<w;x++){
      const i=(y*w+x)*4;
      const a=d[i+3];
      if (a < 10) { mask[y*w+x]=0; continue; }
      const dr=d[i]-bg.r, dg=d[i+1]-bg.g, db=d[i+2]-bg.b;
      const dist = Math.sqrt(dr*dr+dg*dg+db*db);
      mask[y*w+x] = dist > thr ? 1 : 0;
    }
  }

  // Dilate a bit to preserve thin strokes / anti-aliasing
  const fat = _dilateMask(mask,w,h,1);

  // Apply alpha with a tiny feather near the threshold
  for (let y=0;y<h;y++){
    for (let x=0;x<w;x++){
      const idx=y*w+x;
      const i=idx*4;
      const a=d[i+3];
      if (a < 10) continue;
      if (fat[idx]) continue;

      const dr=d[i]-bg.r, dg=d[i+1]-bg.g, db=d[i+2]-bg.b;
      const dist = Math.sqrt(dr*dr+dg*dg+db*db);
      // soft edge: fade from (thr-6 .. thr+6)
      const t = _clamp01((dist - (thr-6)) / 12);
      const keep = t; // 0 = background, 1 = keep
      d[i+3] = Math.round(a * keep);
    }
  }

  ctx.putImageData(im,0,0);
  return c;
}

function _drawContain(ctx, srcCanvasOrImg){
  const cw = ctx.canvas.width, ch = ctx.canvas.height;
  const sw = srcCanvasOrImg.width || srcCanvasOrImg.naturalWidth || 1;
  const sh = srcCanvasOrImg.height || srcCanvasOrImg.naturalHeight || 1;
  const s = Math.min(cw / sw, ch / sh);
  const dw = sw * s;
  const dh = sh * s;
  const dx = (cw - dw) / 2;
  const dy = (ch - dh) / 2;
  ctx.clearRect(0,0,cw,ch);
  ctx.drawImage(srcCanvasOrImg, dx, dy, dw, dh);
}
// ---------------------------------------------------------------------------




function emitNotify(detail) {
  try {
    window.dispatchEvent(new CustomEvent("ps:notify", { detail }));
  } catch {}
}

function accountKeyFromUser(user){
  const id = user?.id != null ? String(user.id) : "";
  // backend may already prefix ids with "u_". Don't double-prefix.
  if (id) {
    if (/^(u|e)_/i.test(id)) return id;
    return `u_${id}`;
  }
  const em = String(user?.email || "").trim().toLowerCase();
  return em ? `e_${em}` : "guest";
}

async function downloadAsPng(url, filename) {
  const u = resolveUrl(url);
  const resp = await fetch(u, { credentials: "include" });
  if (!resp.ok) throw new Error(`download failed: ${resp.status}`);
  const blob = await resp.blob();
  const a = document.createElement("a");
  const obj = URL.createObjectURL(blob);
  a.href = obj;
  a.download = filename || "image.png";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(obj), 1500);
}

async function downloadManyAsZip(urls, zipName) {
  // Lazy import so project doesn't break if ZIP isn't used often
  const JSZipMod = await import("jszip");
  const JSZip = JSZipMod.default || JSZipMod;
  const zip = new JSZip();
  let i = 1;
  for (const u0 of urls) {
    const u = resolveUrl(u0);
    const resp = await fetch(u, { credentials: "include" });
    if (!resp.ok) throw new Error(`download failed: ${resp.status}`);
    const blob = await resp.blob();
    const ext = (blob.type && blob.type.includes("png")) ? "png" : (blob.type && blob.type.includes("jpeg")) ? "jpg" : "bin";
    zip.file(`favorite_${String(i).padStart(2,"0")}.${ext}`, blob);
    i += 1;
  }
  const outBlob = await zip.generateAsync({ type: "blob" });
  const a = document.createElement("a");
  const obj = URL.createObjectURL(outBlob);
  a.href = obj;
  a.download = zipName || "favorites.zip";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(obj), 1500);
}

async function fileToDataUrl(file) {
  return await new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ""));
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

async function saveDataUrlToAssets(dataUrl) {
  const out = await fetchJson("/api/assets/fromDataUrl", {
    method: "POST",
    body: { dataUrl },
  });
  return out?.url || "";
}

async function saveUrlToAssets(url) {
  const out = await fetchJson("/api/assets/fromUrl", {
    method: "POST",
    body: { url },
  });
  return out?.url || "";
}

function downloadUrl(url, filename = "result.png") {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const APPLY_TYPES = [
  { key: "print_dtf", label: "Принт (DTF)" },
  { key: "screen_print", label: "Шелкография" },
  { key: "htv_vinyl", label: "Термоплёнка (HTV)" },
  { key: "sublimation", label: "Сублимация" },
  { key: "embroidery", label: "Вышивка" },
  { key: "chenille_patch", label: "Шенилл нашивка" },
  { key: "woven_label", label: "Тканая бирка" },
  { key: "patch", label: "Патч / Шеврон" },
  { key: "rubber_patch", label: "Резиновый патч" },
  { key: "metal_badge", label: "Металлическая нашивка" },
  { key: "tattoo", label: "Тату (на кожу)" },
  { key: "foil", label: "Фольга / Металлик" },
  { key: "rhinestones", label: "Стразы" },
  { key: "deboss", label: "Тиснение" },
  { key: "laser", label: "Лазерная гравировка" },
];


function ApplyTypeSelect({ value, onChange, items, disabled }) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef(null);
  const btnRef = React.useRef(null);
  const [menuRect, setMenuRect] = React.useState(null);

  // Close on outside click
  React.useEffect(() => {
    const onDoc = (e) => {
      if (!rootRef.current) return;
      if (!rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  // When opening, measure button and keep menu within viewport.
  React.useLayoutEffect(() => {
    if (!open) return;
    const el = btnRef.current;
    if (!el) return;

    const r = el.getBoundingClientRect();
    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;

    const desiredW = r.width;
    const maxH = Math.min(360, Math.max(220, vh - 24));
    const left = Math.max(12, Math.min(r.left, vw - desiredW - 12));
    const top = Math.max(12, Math.min(r.bottom + 8, vh - maxH - 12));

    setMenuRect({ left, top, width: desiredW, maxHeight: maxH });
  }, [open]);

  const current = items.find((x) => x.key === value) || items[0];

  return (
    <div className={"psSelect " + (disabled ? "disabled" : "")} ref={rootRef}>
      <button
        ref={btnRef}
        type="button"
        className="psSelectBtn"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
      >
        <span className="psSelectVal">{current?.label || "—"}</span>
        <span className="psSelectChevron">▾</span>
      </button>

      {open &&
        menuRect &&
        ReactDOM.createPortal(
          <div className="psSelectPortal" onMouseDown={(e) => e.stopPropagation()}>
            <div
              className="psSelectPortalMenu"
              style={{
                left: menuRect.left,
                top: menuRect.top,
                width: menuRect.width,
                maxHeight: menuRect.maxHeight,
              }}
            >
              {items.map((it) => (
                <button
                  key={it.key}
                  type="button"
                  className={"psSelectItem " + (it.key === value ? "active" : "")}
                  onClick={() => {
                    onChange(it.key);
                    setOpen(false);
                  }}
                >
                  {it.label}
                </button>
              ))}
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}



export default function PrintsPage() {
  const { user, loading, refresh } = useAuth();
  const accountKey = React.useMemo(() => accountKeyFromUser(user), [user]);
  const didHydrateRef = React.useRef(false);

  // inputs
  const [garmentUrl, setGarmentUrl] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [baseLocked, setBaseLocked] = React.useState(false);
const [baseBeforePreview, setBaseBeforePreview] = React.useState("");
const [garmentSlots, setGarmentSlots] = React.useState(() => Array(9).fill(""));
  const [activeGarmentIdx, setActiveGarmentIdx] = React.useState(0);

  // auto-pick first filled garment slot so stage/marker appears without extra clicks
  React.useEffect(() => {
    if (!garmentSlots || !garmentSlots.length) return;
    const hasActive = !!garmentSlots[activeGarmentIdx];
    if (hasActive) return;
    const first = garmentSlots.findIndex((u) => !!u);
    if (first >= 0 && first !== activeGarmentIdx) setActiveGarmentIdx(first);
  }, [garmentSlots, activeGarmentIdx]);

  // Keep existing garmentUrl for minimal diff: it always mirrors the active slot
  React.useEffect(() => {
    const u = garmentSlots[activeGarmentIdx] || "";
    if (u !== garmentUrl) setGarmentUrl(u);
        if (!baseLocked) setBaseUrl(u);
// eslint-disable-next-line react-hooks/exhaustive-deps
  }, [garmentSlots, activeGarmentIdx, baseLocked]);

  function _normalizeSlots9(arr) {
    const out = (arr || []).filter((x) => !!x).slice(0, 9);
    while (out.length < 9) out.push("");
    return out;
  }

  function addGarment(url) {
    const u = url || "";
    if (!u) return;
    setGarmentSlots((prev) => {
      const p = (prev || []).slice(0, 9);
      while (p.length < 9) p.push("");
      const firstEmpty = p.findIndex((x) => !x);
      const idx = firstEmpty >= 0 ? firstEmpty : clamp(activeGarmentIdx, 0, 8);
      const next = p.slice();
      next[idx] = u;
      queueMicrotask(() => {
        setActiveGarmentIdx(idx);
        setGarmentUrl(next[idx] || "");
      });
      return next;
    });
  }

  function deleteGarment(idx) {
    setGarmentSlots((prev) => {
      const p = (prev || []).slice(0, 9);
      while (p.length < 9) p.push("");
      if (!p[idx]) return p;
      p[idx] = "";
      const compacted = _normalizeSlots9(p);
      const firstEmpty = compacted.findIndex((x) => !x);
      const newActive = firstEmpty === 0 ? 0 : clamp((firstEmpty === -1 ? 8 : firstEmpty - 1), 0, 8);
      queueMicrotask(() => {
        setActiveGarmentIdx(newActive);
        setGarmentUrl(compacted[newActive] || "");
      });
      return compacted;
    });
  }

  function _clearGarmentsOnly() {
    setGarmentSlots(Array(9).fill(""));
    setActiveGarmentIdx(0);
  }

  function clearAllGarments() {
    _clearGarmentsOnly();
    setGarmentUrl("");
    setSelectedResultId(null);
  }
  const [logoUrl, setLogoUrl] = React.useState("");
  const [logoSlots, setLogoSlots] = React.useState(() => Array(9).fill(""));
  const [activeLogoIdx, setActiveLogoIdx] = React.useState(0);

  React.useEffect(() => {
    if (!logoSlots || !logoSlots.length) return;
    const hasActive = !!logoSlots[activeLogoIdx];
    if (hasActive) return;
    const first = logoSlots.findIndex((u) => !!u);
    if (first >= 0 && first !== activeLogoIdx) setActiveLogoIdx(first);
  }, [logoSlots, activeLogoIdx]);

  // Keep existing logoUrl for minimal diff: it always mirrors the active slot
  React.useEffect(() => {
    const u = logoSlots[activeLogoIdx] || "";
    if (u !== logoUrl) setLogoUrl(u);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logoSlots, activeLogoIdx]);

  function _normalizeLogoSlots9(arr) {
    const out = (arr || []).filter((x) => !!x).slice(0, 9);
    while (out.length < 9) out.push("");
    return out;
  }

  function addLogo(url) {
    const u = url || "";
    if (!u) return;
    setLogoSlots((prev) => {
      const p = (prev || []).slice(0, 9);
      while (p.length < 9) p.push("");
      const firstEmpty = p.findIndex((x) => !x);
      const idx = firstEmpty >= 0 ? firstEmpty : clamp(activeLogoIdx, 0, 8);
      const next = p.slice();
      next[idx] = u;
      queueMicrotask(() => {
        setActiveLogoIdx(idx);
        setLogoUrl(next[idx] || "");
      });
      return next;
    });
  }

  function deleteLogo(idx) {
    setLogoSlots((prev) => {
      const p = (prev || []).slice(0, 9);
      while (p.length < 9) p.push("");
      if (!p[idx]) return p;
      p[idx] = "";
      const compacted = _normalizeLogoSlots9(p);
      const firstEmpty = compacted.findIndex((x) => !x);
      const newActive = firstEmpty === 0 ? 0 : clamp((firstEmpty === -1 ? 8 : firstEmpty - 1), 0, 8);
      queueMicrotask(() => {
        setActiveLogoIdx(newActive);
        setLogoUrl(compacted[newActive] || "");
      });
      return compacted;
    });
  }

  function clearAllLogos() {
    setLogoSlots(Array(9).fill(""));
    setActiveLogoIdx(0);
    setLogoUrl("");
  }

  // placement (normalized 0..1) RELATIVE TO THE REAL IMAGE CONTENT (not the stage).
  // This avoids drift when the stage uses object-fit: contain (letterbox).
  const [bbox, setBbox] = React.useState({ x: 0.35, y: 0.32, w: 0.28, h: 0.18 });
  const [markerBendDeg, setMarkerBendDeg] = React.useState(0); // legacy 3D tilt (may be reused later)
  const [markerCurve, setMarkerCurve] = React.useState(0); // -1..1 : vertical warp (arc)
  const [markerWarp, setMarkerWarp] = React.useState({ top: 0, right: 0, bottom: 0, left: 0 }); // -1..1 per side
  const pinDragRef = React.useRef(null);

  const _clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  const beginPinDrag = (e, op) => {
    try { e.preventDefault(); } catch {}
    try { e.stopPropagation(); } catch {}
    if (!bbox) return;
    const p = clientToImage01(e.clientX, e.clientY);
    if (!p) return;
    pinDragRef.current = {
      op,
      startP: p,
      startBbox: { ...bbox },
      startBend: markerBendDeg,
      startCurve: markerCurve,
      startWarp: { ...(markerWarp || { top:0,right:0,bottom:0,left:0 }) },
    };
    const onMove = (ev) => {
      const st = pinDragRef.current;
      if (!st) return;
      try { ev.preventDefault(); } catch {}
      const q = clientToImage01(ev.clientX, ev.clientY);
      if (!q) return;
      const dx = q.x - st.startP.x;
      const dy = q.y - st.startP.y;
      if (st.op === "move") {
        const nx = _clamp(st.startBbox.x + dx, 0, 1 - st.startBbox.w);
        const ny = _clamp(st.startBbox.y + dy, 0, 1 - st.startBbox.h);
        setBbox({ ...st.startBbox, x: nx, y: ny });
      } else if (st.op === "scale") {
        const nw = _clamp(q.x - st.startBbox.x, 0.03, 1 - st.startBbox.x);
        const nh = _clamp(q.y - st.startBbox.y, 0.03, 1 - st.startBbox.y);
        setBbox({ ...st.startBbox, w: nw, h: nh });
      } else if (st.op === "height") {
        const nh = _clamp(q.y - st.startBbox.y, 0.03, 1 - st.startBbox.y);
        setBbox({ ...st.startBbox, h: nh });
      } else if (st.op === "bend" || st.op === "bendBottom") {
        // bottom bend (legacy): drag up/down changes curvature
        const next = _clamp(st.startCurve - dy * 2.2, -1, 1);
        setMarkerCurve(next);
        setMarkerWarp((p) => ({ ...(p || {}), bottom: next }));
      } else if (st.op === "bendTop") {
        // top bend: drag up/down
        const base = st.startWarp || { top: 0, right: 0, bottom: 0, left: 0 };
        const next = _clamp((base.top || 0) - dy * 2.2, -1, 1);
        setMarkerWarp((p) => ({ ...(p || {}), top: next }));
      } else if (st.op === "bendLeft") {
        // left bend: drag left/right (dx)
        const base = st.startWarp || { top: 0, right: 0, bottom: 0, left: 0 };
        const next = _clamp((base.left || 0) - dx * 2.2, -1, 1);
        setMarkerWarp((p) => ({ ...(p || {}), left: next }));
      } else if (st.op === "bendRight") {
        // right bend: drag left/right (dx)
        const base = st.startWarp || { top: 0, right: 0, bottom: 0, left: 0 };
        const next = _clamp((base.right || 0) + dx * 2.2, -1, 1);
        setMarkerWarp((p) => ({ ...(p || {}), right: next }));
      }
    };
    const onUp = (ev) => {
      try { ev.preventDefault(); } catch {}
      pinDragRef.current = null;
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove, { passive: false });
    window.addEventListener("pointerup", onUp, { passive: false });
  };
  const [isDrawing, setIsDrawing] = React.useState(false);
  const startRef = React.useRef({ x: 0, y: 0 });
  const [dragMove, setDragMove] = React.useState(null); // {sx,sy, ox,oy}

  // mode
  const [applyType, setApplyType] = React.useState("print_dtf");
  const [rotationDeg, setRotationDeg] = React.useState(0);
  const [rotArrowFlash, setRotArrowFlash] = React.useState("");

  const [rotHot, setRotHot] = React.useState(false);
  const rotHotTimerRef = React.useRef(null);
  const lastHotGarmentRef = React.useRef(null);

  function triggerRotHot(){
    try { if (rotHotTimerRef.current) clearTimeout(rotHotTimerRef.current); } catch(e) {}
    setRotHot(true);
    rotHotTimerRef.current = setTimeout(() => setRotHot(false), 2600);
  }
// strict placement lock (backend should NOT move the design away from the box)

  const [placementLock, setPlacementLock] = React.useState(true);

  // results
  const [results, setResults] = React.useState([]); 
  
  const [previewResult, setPreviewResult] = React.useState(null);
const [activeHistoryIdx, setActiveHistoryIdx] = React.useState(0);
// [{id,url,ts,applyType}]
  const [selectedResultId, setSelectedResultId] = React.useState(null);
  

  React.useEffect(() => {
    // keep activeHistoryIdx in sync with selectedResultId
    if (!selectedResultId || selectedResultId === "preview") return;
    const idx = results.findIndex((r) => r.id === selectedResultId);
    if (idx >= 0) setActiveHistoryIdx(Math.min(idx, 8));
  }, [selectedResultId, results]); // psHistorySync
const [lightboxUrl, setLightboxUrl] = React.useState("");

  // ui
  const [busy, setBusy] = React.useState(false);
  const [busyPhrase, setBusyPhrase] = React.useState("");
  const busyTimerRef = React.useRef(null);
  const [status, setStatus] = React.useState("");

  // --- Persist (account-scoped) ---
  const authLoading = !!loading;
  const KEY_PRINTS_STATE = React.useMemo(() => `psw_prints_state:${accountKey}`, [accountKey]);
  const KEY_PRINTS_INFLIGHT = React.useMemo(() => `psw_prints_inflight:${accountKey}`, [accountKey]);

  const debugPersist = typeof window !== "undefined" && /[?&]debug(?:=1|=true|&|$)/i.test(window.location.search || "");
  const dbg = React.useCallback((tag, obj) => {
    if (!debugPersist) return;
    try { console.log(`[PRINTS] ${tag}`, obj ?? ""); } catch(e) {}
  }, [debugPersist]);

  const hasAnyData = React.useCallback((st) => {
    if (!st || typeof st !== "object") return false;
    const hasSlots = (arr) => Array.isArray(arr) && arr.some((x) => typeof x === "string" && x.trim());
    if (hasSlots(st.garmentSlots)) return true;
    if (hasSlots(st.logoSlots)) return true;
    if (Array.isArray(st.results) && st.results.length) return true;
    if (typeof st.baseUrl === "string" && st.baseUrl.trim()) return true;
    if (st.previewResult && typeof st.previewResult === "object" && st.previewResult.url) return true;
    return false;
  }, []);
  const isEmptyState = React.useCallback((st) => !hasAnyData(st), [hasAnyData]);

  React.useEffect(() => {
    if (authLoading) return;
    // hydrate on account change (strict-mode safe)
    didHydrateRef.current = false;
    try {
      const raw = localStorage.getItem(KEY_PRINTS_STATE);
      dbg("hydrate:raw", { accountKey, key: KEY_PRINTS_STATE, len: raw ? raw.length : 0 });
      if (!raw) {
        didHydrateRef.current = true;
        return;
      }
      const st = JSON.parse(raw);
      if (st && typeof st === "object") {
        if (Array.isArray(st.garmentSlots)) setGarmentSlots(_normalizeSlots9(st.garmentSlots));
        if (typeof st.activeGarmentIdx === "number") setActiveGarmentIdx(clamp(st.activeGarmentIdx, 0, 8));
        if (typeof st.baseLocked === "boolean") setBaseLocked(st.baseLocked);
        if (typeof st.baseUrl === "string") setBaseUrl(st.baseUrl);
        if (typeof st.baseBeforePreview === "string") setBaseBeforePreview(st.baseBeforePreview);

        if (Array.isArray(st.logoSlots)) setLogoSlots(_normalizeSlots9(st.logoSlots));
        if (typeof st.activeLogoIdx === "number") setActiveLogoIdx(clamp(st.activeLogoIdx, 0, 8));

        if (st.bbox && typeof st.bbox === "object") {
          setBbox({
            x: clamp(Number(st.bbox.x ?? 0.3), 0, 1),
            y: clamp(Number(st.bbox.y ?? 0.3), 0, 1),
            w: clamp(Number(st.bbox.w ?? 0.3), 0.001, 1),
            h: clamp(Number(st.bbox.h ?? 0.3), 0.001, 1),
          });
        }
        if (typeof st.rotationDeg === "number") setRotationDeg(deltaToRot(st.rotationDeg));
        if (typeof st.markerCurve === "number") setMarkerCurve(clamp(st.markerCurve, -1, 1));
        if (st.markerWarp && typeof st.markerWarp === "object") {
          setMarkerWarp({
            top: clamp(Number(st.markerWarp.top ?? 0), -1, 1),
            right: clamp(Number(st.markerWarp.right ?? 0), -1, 1),
            bottom: clamp(Number(st.markerWarp.bottom ?? st.markerCurve ?? 0), -1, 1),
            left: clamp(Number(st.markerWarp.left ?? 0), -1, 1),
          });
        } else {
          // backward compat: old state used markerCurve as bottom bend
          setMarkerWarp((p) => ({ ...p, bottom: clamp(Number(st.markerCurve ?? 0), -1, 1) }));
        }
        if (typeof st.applyType === "string") setApplyType(st.applyType);
        if (typeof st.placementLock === "boolean") setPlacementLock(st.placementLock);

        if (Array.isArray(st.results)) setResults(st.results.filter(Boolean).slice(0, 9));
        if (typeof st.activeHistoryIdx === "number") setActiveHistoryIdx(clamp(st.activeHistoryIdx, 0, 8));
        if (typeof st.selectedResultId === "string" || st.selectedResultId === null) setSelectedResultId(st.selectedResultId);
        if (st.previewResult && typeof st.previewResult === "object") setPreviewResult(st.previewResult);
      }
    } catch (e) {
      console.warn("PrintsPage hydrate failed", e);
    } finally {
      didHydrateRef.current = true;
      dbg("hydrate:done", { accountKey, key: KEY_PRINTS_STATE });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [KEY_PRINTS_STATE, authLoading, accountKey, dbg]);

  React.useEffect(() => {
    if (authLoading) return;
    if (!didHydrateRef.current) return;
    try {
      const st = {
        garmentSlots,
        activeGarmentIdx,
        baseLocked,
        baseUrl,
        baseBeforePreview,
        logoSlots,
        activeLogoIdx,
        bbox,
        rotationDeg,
        markerCurve,
        markerWarp,
        applyType,
        placementLock,
        results,
        activeHistoryIdx,
        selectedResultId,
        previewResult,
      };

      // prevent accidental wipe: if we're about to store an empty state but existing has data, skip.
      const nextIsEmpty = isEmptyState(st);
      if (nextIsEmpty) {
        try {
          const prevRaw = localStorage.getItem(KEY_PRINTS_STATE);
          if (prevRaw) {
            const prev = JSON.parse(prevRaw);
            if (hasAnyData(prev)) {
              dbg("save:skip_wipe", { accountKey, key: KEY_PRINTS_STATE });
              return;
            }
          }
        } catch(e) {}
      }

      const raw = JSON.stringify(st);
      localStorage.setItem(KEY_PRINTS_STATE, raw);
      dbg("save", {
        accountKey,
        key: KEY_PRINTS_STATE,
        bytes: raw.length,
        g0: garmentSlots?.[0] ? String(garmentSlots[0]).slice(0, 80) : "",
        l0: logoSlots?.[0] ? String(logoSlots[0]).slice(0, 80) : "",
        rN: Array.isArray(results) ? results.length : 0,
      });
    } catch (e) {
      // ignore quota errors
    }
  }, [
    KEY_PRINTS_STATE,
    authLoading,
    accountKey,
    garmentSlots,
    activeGarmentIdx,
    baseLocked,
    baseUrl,
    baseBeforePreview,
    logoSlots,
    activeLogoIdx,
    bbox,
    rotationDeg,
    markerCurve,
    applyType,
    placementLock,
    results,
    activeHistoryIdx,
    selectedResultId,
    previewResult,
    hasAnyData,
    isEmptyState,
    dbg,
  ]);

  const [logItems, setLogItems] = React.useState([]); // newest first
  const pushLog = React.useCallback((kind, text) => {
    const msg = String(text || "");
    if (!msg) return;
    const item = { id: `${Date.now()}_${Math.random().toString(16).slice(2)}`, kind: kind || "info", text: msg, ts: Date.now() };
    setLogItems((arr) => [item, ...arr].slice(0, 120));
  }, []);

  const log = React.useCallback((text, kind = "info") => {
    setStatus(String(text || ""));
    pushLog(kind, text);
  }, [pushLog]);


  // URL modal (same UX as Lookbook/Scene/Video — no browser prompt)
  const urlModalSetterRef = React.useRef(null);
  const [urlModal, setUrlModal] = React.useState({ open: false, title: "URL изображения", value: "", error: "" });

  function openUrlModal(title, setter) {
    urlModalSetterRef.current = typeof setter === "function" ? setter : null;
    setUrlModal({ open: true, title: title || "URL изображения", value: "", error: "" });
  }

  function closeUrlModal() {
    urlModalSetterRef.current = null;
    setUrlModal({ open: false, title: "URL изображения", value: "", error: "" });
  }

  async function confirmUrlModal() {
    const u = String(urlModal.value || "").trim();
    if (!u) {
      setUrlModal((p) => ({ ...p, error: "Вставь ссылку." }));
      return;
    }
    const setter = urlModalSetterRef.current;
    if (!setter) {
      closeUrlModal();
      return;
    }
    log("Загрузка URL...", "info");
    try {
      const stored = await saveUrlToAssets(u);
      if (!stored) throw new Error("empty");
      setter(stored);
      setStatus("");
      // очистить поле после ввода
      setUrlModal({ open: false, title: "URL изображения", value: "", error: "" });
      urlModalSetterRef.current = null;
    } catch (e) {
      setStatus("");
      setUrlModal((p) => ({ ...p, value: "", error: "Не удалось загрузить. Проверь URL." }));
    }
  }

  const stageRef = React.useRef(null);
  const pinCanvasRef = React.useRef(null);
  const [stageTick, setStageTick] = React.useState(0);
  const pinImgRef = React.useRef(null);
  const didAutoInitMarkerRef = React.useRef(false);

  const selectedResult = React.useMemo(
    () => (selectedResultId === "preview" ? previewResult : results.find((r) => r.id === selectedResultId)) || null,
    [results, selectedResultId, previewResult]
  );

  
      React.useEffect(() => {
    // Highlight rotation block when the base (with marker) appears in the main stage.
    const u = baseUrl || activeGarmentUrl;
    if (!u) return;
    if (lastHotGarmentRef.current !== u) {
      lastHotGarmentRef.current = u;
      triggerRotHot();
    }
  }, [baseUrl, garmentUrl]);

  React.useEffect(() => {
    function onKey(e){
      if (e.key === 'Escape') setLightboxUrl("");
    }
    window.addEventListener('keydown', onKey, { passive: true });
    return () => window.removeEventListener('keydown', onKey);
  }, []); // psLightboxEsc

const firstGarmentUrl = (garmentSlots && garmentSlots.find((u)=>!!u)) || garmentUrl || "";
  const safeGarmentIdx = (garmentSlots && garmentSlots[activeGarmentIdx]) ? activeGarmentIdx : (garmentSlots ? Math.max(0, garmentSlots.findIndex((u)=>!!u)) : 0);
  const activeGarmentUrl = (garmentSlots && garmentSlots[safeGarmentIdx]) || garmentUrl || "";
  const mainImageUrl = baseUrl || activeGarmentUrl;

  const firstLogoUrl = (logoSlots && logoSlots.find((u)=>!!u)) || logoUrl || "";
  const safeLogoIdx = (logoSlots && logoSlots[activeLogoIdx]) ? activeLogoIdx : (logoSlots ? Math.max(0, logoSlots.findIndex((u)=>!!u)) : 0);
  const markerLogoUrl = (logoSlots && logoSlots[safeLogoIdx]) || logoUrl || "";


  // Draw logo preview inside the marker (preview-only), removing background from any logo image.
  React.useEffect(() => {
    // moved into curved preview renderer below
    return;

    const canvas = pinCanvasRef.current;
    if (!canvas) return;
    if (!markerLogoUrl) {
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvas.width || 0, canvas.height || 0);
      return;
    }

    let cancelled = false;
    (async () => {
      try {
        const img = await _loadImg(markerLogoUrl);
        if (cancelled) return;

        // Ensure a stable canvas resolution for crisp preview
        const target = 420;
        canvas.width = target;
        canvas.height = target;

        const masked = _removeBgPreview(img);
        if (cancelled) return;

        const ctx = canvas.getContext("2d", { alpha: true, willReadFrequently: false });
        if (!ctx) return;
        _drawContain(ctx, masked);
      } catch (e) {
        // Fallback: just draw the image as-is
        try {
          const img = await _loadImg(markerLogoUrl);
          if (cancelled) return;
          const target = 420;
          canvas.width = target;
          canvas.height = target;
          const ctx = canvas.getContext("2d", { alpha: true });
          if (!ctx) return;
          _drawContain(ctx, img);
        } catch {}
      }
    })();

    return () => { cancelled = true; };
  }, [markerLogoUrl]);


  // auto-select first filled garment/logo slot so stage + marker never get "empty active slot"
  React.useEffect(() => {
    if (garmentSlots && garmentSlots.length) {
      const idx = garmentSlots.findIndex((u)=>!!u);
      if (idx >= 0 && !garmentSlots[activeGarmentIdx]) setActiveGarmentIdx(idx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [garmentSlots]);

  React.useEffect(() => {
    if (logoSlots && logoSlots.length) {
      const idx = logoSlots.findIndex((u)=>!!u);
      if (idx >= 0 && !logoSlots[activeLogoIdx]) setActiveLogoIdx(idx);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [logoSlots]);

  React.useEffect(() => {
    didAutoInitMarkerRef.current = false;
  }, [mainImageUrl, stageTick]);

  // Auto-show marker when both base image and logo are loaded (first time only per base).
  React.useEffect(() => {
    if (!mainImageUrl || !markerLogoUrl) return;
    if (didAutoInitMarkerRef.current) return;

    const bad =
      !Number.isFinite(bbox?.x) ||
      !Number.isFinite(bbox?.y) ||
      !Number.isFinite(bbox?.w) ||
      !Number.isFinite(bbox?.h) ||
      bbox.w < 0.02 ||
      bbox.h < 0.02;

    if (bad) {
      setBbox((prev) => ({
        ...prev,
        x: 0.35,
        y: 0.32,
        w: 0.28,
        h: 0.18,
      }));
    }
    didAutoInitMarkerRef.current = true;
  }, [mainImageUrl, markerLogoUrl, activeLogoIdx]);


  React.useEffect(() => {
    // auto defaults per mode
    if (applyType === "sublimation") {
      // sublimation is usually "cover area", allow more freedom
      setPlacementLock(false);
    } else {
      // for DTF/patches/etc default to strict
      setPlacementLock(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyType]);

  function getImageBox() {
    const stage = stageRef.current;
    if (!stage) return null;
    const img = stage.querySelector("img.stageImg");
    if (!img) return null;

    const sr = stage.getBoundingClientRect();
    const stageW = sr.width || 1;
    const stageH = sr.height || 1;
    const natW = img.naturalWidth || 0;
    const natH = img.naturalHeight || 0;
    if (!natW || !natH) return null;

    // object-fit: contain => rendered image content is centered with possible letterbox
    const scale = Math.min(stageW / natW, stageH / natH);
    const contentW = natW * scale;
    const contentH = natH * scale;
    const offX = (stageW - contentW) / 2;
    const offY = (stageH - contentH) / 2;

    return { stageRect: sr, stageW, stageH, natW, natH, scale, contentW, contentH, offX, offY };
  }

  function clientToImage01(evOrX, maybeY) {
    const box = getImageBox();
    if (!box) return null;
    const { stageRect: r, offX, offY, contentW, contentH } = box;
    if (!contentW || !contentH) return null;

    const clientX = typeof evOrX === "number" ? evOrX : evOrX?.clientX;
    const clientY = typeof evOrX === "number" ? maybeY : evOrX?.clientY;
    if (typeof clientX !== "number" || typeof clientY !== "number") return null;

    const pxX = clientX - r.left;
    const pxY = clientY - r.top;
    const x = clamp((pxX - offX) / contentW, 0, 1);
    const y = clamp((pxY - offY) / contentH, 0, 1);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
  }

  function onStageDown(e) {
    if (busy) return;
    if (!mainImageUrl) return;
    // рисуем метку только по базовому фото (не по результату)

    const pt = clientToImage01(e);
    if (!pt) return;
    const { x, y } = pt;
    const inside =
      x >= bbox.x &&
      x <= bbox.x + bbox.w &&
      y >= bbox.y &&
      y <= bbox.y + bbox.h;

    if (inside) {
      // move
      setDragMove({ sx: x, sy: y, ox: bbox.x, oy: bbox.y });
      return;
    }

    // start new box
    triggerRotHot();
    setRotationDeg(0); // IMPORTANT: do not "remember" previous rotation for a fresh marker
    startRef.current = { x, y };
    setIsDrawing(true);
    setBbox({ x, y, w: 0.001, h: 0.001 });
  }

  function onStageMove(e) {
    if (busy) return;
    if (!mainImageUrl) return;

    const pt = clientToImage01(e);
    if (!pt) return;
    const { x, y } = pt;

    if (dragMove) {
      const dx = x - dragMove.sx;
      const dy = y - dragMove.sy;
      const nx = clamp(dragMove.ox + dx, 0, 1 - bbox.w);
      const ny = clamp(dragMove.oy + dy, 0, 1 - bbox.h);
      setBbox((b) => ({ ...b, x: nx, y: ny }));
      return;
    }

    if (!isDrawing) return;

    const sx = startRef.current.x;
    const sy = startRef.current.y;
    const x0 = Math.min(sx, x);
    const y0 = Math.min(sy, y);
    const w = Math.max(0.01, Math.abs(x - sx));
    const h = Math.max(0.01, Math.abs(y - sy));
    setBbox({ x: x0, y: y0, w: clamp(w, 0.01, 1), h: clamp(h, 0.01, 1) });
  }

  function onStageUp() {
    if (busy) return;
    setIsDrawing(false);
    setDragMove(null);
  }


  function onStageWheel(e){
    if (busy) return;
    if (!garmentUrl) return;
    if (!e.shiftKey) return;
    e.preventDefault();
    const delta = e.deltaY > 0 ? -5 : 5;
    setRotationDeg((d)=> ((d + delta) % 360 + 360) % 360);
  }

  async function pickFile(setter) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = async () => {
      const f = input.files && input.files[0];
      if (!f) return;
      log("Загрузка...", "info");
      try {
        const dataUrl = await fileToDataUrl(f);
        const url = await saveDataUrlToAssets(dataUrl);
        setter(url);
        setStatus("");
      } catch (e) {
        log("Ошибка загрузки", "err");
      }
    };
    input.click();
  }

  function pickUrl(setter, title = "URL изображения") {
    openUrlModal(title, setter);
  }

  function clearResultPreview() {
    setSelectedResultId(null);
  }

  function deleteSelectedResult() {
    if (!selectedResult) return;
    const id = selectedResult.id;
    setResults((arr) => arr.filter((r) => r.id !== id));
    setSelectedResultId(null);
  }

  function deleteResultById(id){
    if (!id) return;
    setResults((arr) => arr.filter((r) => r.id !== id));
    setSelectedResultId((cur) => (cur === id ? null : cur));
  }

  
  function placementForSend() {
    // bbox is already stored in IMAGE-relative 0..1 coords.
    return {
      x: clamp(bbox.x, 0, 1),
      y: clamp(bbox.y, 0, 1),
      w: clamp(bbox.w, 0.001, 1),
      h: clamp(bbox.h, 0.001, 1),
    };
  }


async function onGenerate() {
    if (!mainImageUrl) {
      log("Добавь фото одежды", "info");
      return;
    }
    const activeLogoUrl = logoSlots[activeLogoIdx] || logoUrl || "";
    if (!activeLogoUrl) {
      log("Добавь логотип", "info");
      return;
    }
    const phrases = [
      "Разогреваю термопресс… не трогай, горячо!",
      "Выравниваю лого по уровню космоса…",
      "Снимаю фон, чтобы не было ‘ореола святости’.",
      "Подбираю режим: DTF, вышивка, фольга… будто выбираю кофе.",
      "Прижимаю аккуратно — без пузырей и паники.",
      "Проверяю края: чтобы не было ‘двойного контура’.",
      "Натягиваю ткань мысленно — и физика соглашается.",
      "Считаю до трёх… и печатаю!",
      "Пиксели строем! Ровно по метке!",
      "Ещё чуть-чуть — и будет как на витрине.",
      "Если что — сделаем ретрай. Но тихо…",
      "Ловлю идеальный изгиб, как будто это капот на автошоу.",
      "Контроль качества: ‘норм, можно в прод’.",
      "Финальный прижим… и отпускаю."
    ];

    setBaseBeforePreview(mainImageUrl);
    setBusy(true);
    setBusyPhrase(phrases[0]);
    log("Готовлю нанесение…", "info");
    try { if (busyTimerRef.current) clearInterval(busyTimerRef.current); } catch(e) {}
    busyTimerRef.current = setInterval(() => {
      setBusyPhrase((cur) => {
        const idx = Math.max(0, phrases.indexOf(cur));
        const next = phrases[(idx + 1) % phrases.length];
        return next;
      });
    }, 4500);

    // helper: fetch with timeout (prevents "зависло" forever)
    const fetchJsonWithTimeout = async (path, opts, timeoutMs = 180000) => {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), timeoutMs);
      try {
        return await fetchJson(path, { ...(opts || {}), signal: ctrl.signal });
      } finally {
        clearTimeout(t);
      }
    };

    try {
      // Idempotency key: backend uses it to avoid double-spending credits on reload/login
      // and to return cached result for the same apply attempt.
      const requestId = `prn_${Date.now()}_${Math.random().toString(16).slice(2)}`;

      // backend expects baseUrl/designUrl + placement bbox + mode
      const payload = {
        requestId,
        baseUrl: resolveUrl(mainImageUrl),
        designUrl: resolveUrl(activeLogoUrl),
        placement: placementForSend(), // normalized bbox relative to image
        mode: applyType,
        warp: { ...markerWarp, ...computeFitFromWarp(markerWarp) },
        options: { rotationDeg, placementLock, curve: markerCurve, warp: { ...markerWarp, ...computeFitFromWarp(markerWarp) } },
      };
      try { localStorage.setItem(KEY_PRINTS_INFLIGHT, JSON.stringify({ ts: Date.now(), payload })); dbg("inflight:set", { key: KEY_PRINTS_INFLIGHT, bytes: JSON.stringify({ts:Date.now(), payload}).length }); } catch(e) {}
      const out = await fetchJsonWithTimeout("/api/prints/apply", { method: "POST", body: payload }, 180000);
      // backend списывает кредит сам, поэтому обновим кредиты в шапке
      try { await refresh(); } catch(e) {}
      const resultUrl = resolveUrl(out?.resultUrl || out?.url || out?.result || "");
      if (!resultUrl) throw new Error("Пустой результат от /api/prints/apply");

      const item = { id: `${Date.now()}_${Math.random().toString(16).slice(2)}`, url: resultUrl, ts: Date.now(), applyType };
      setPreviewResult(item);
      setSelectedResultId("preview");
      setStatus("");
      emitNotify({
        id: `prints_done_${Date.now()}`,
        kind: "success",
        title: "Генерация готова",
        message: "Принт успешно нанесён. Можешь сохранить результат или продолжить.",
        source: { studioKey: "prints" },
        ttlMs: 9000,
      });
    } catch (e) {
      const msg = String(e?.message || e || "Ошибка генерации");
      setStatus(msg);
      log(msg, "error");
      // refund выполняет backend (если он успел списать)
      try { await refresh(); } catch(e2) {}
    } finally {
      try { localStorage.removeItem(KEY_PRINTS_INFLIGHT); dbg("inflight:clear", { key: KEY_PRINTS_INFLIGHT }); } catch(e) {}
      setBusy(false);
      setBusyPhrase("");
      try { if (busyTimerRef.current) clearInterval(busyTimerRef.current); } catch(e) {}
      busyTimerRef.current = null;
    }
  }

  
  // Resume inflight generation after logout/login or refresh
  const resumedOnceRef = React.useRef(false);
  React.useEffect(() => {
    if (authLoading) return;
    if (busy) return;
    if (resumedOnceRef.current) return;
    try {
      const raw = localStorage.getItem(KEY_PRINTS_INFLIGHT);
      if (!raw) return;
      const inflight = JSON.parse(raw);
      if (!inflight || !inflight.payload) return;

      const ageMs = Date.now() - (inflight.ts || 0);
      // don't resume very old jobs
      if (ageMs > 15 * 60 * 1000) {
        localStorage.removeItem(KEY_PRINTS_INFLIGHT);
        dbg("inflight:clear_old", { key: KEY_PRINTS_INFLIGHT, ageMs });
        return;
      }

      dbg("inflight:resume", { key: KEY_PRINTS_INFLIGHT, ageMs });

      resumedOnceRef.current = true;

      // show overlay again
      setBusy(true);
      setBusyPhrase("Наношу…");
      log("Продолжаю генерацию (после входа/перезагрузки)…", "info");

      // restart phrase ticker (short set)
      const phrases = [
        "Продолжаю печать… держу метку ровно.",
        "Ещё чуть-чуть — закрепляю края.",
        "Слежу за контуром: без двойников!",
        "Финальный прижим…",
        "Почти готово — не моргай!"
      ];
      try { if (busyTimerRef.current) clearInterval(busyTimerRef.current); } catch(e) {}
      busyTimerRef.current = setInterval(() => {
        setBusyPhrase((cur) => {
          const idx = Math.max(0, phrases.indexOf(cur));
          return phrases[(idx + 1) % phrases.length];
        });
      }, 4500);

      const fetchJsonWithTimeout = async (path, opts, timeoutMs = 180000) => {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), timeoutMs);
        try {
          return await fetchJson(path, { ...(opts || {}), signal: ctrl.signal });
        } finally {
          clearTimeout(t);
        }
      };

      (async () => {
        try {
          const out = await fetchJsonWithTimeout("/api/prints/apply", { method: "POST", body: inflight.payload }, 180000);

          const resultUrl = resolveUrl(out?.resultUrl || out?.url || out?.result || out?.imageUrl || "");
          if (!resultUrl) throw new Error("Пустой результат от /api/prints/apply (resume)");

          const item = { id: `${Date.now()}_${Math.random().toString(16).slice(2)}`, url: resultUrl, ts: Date.now(), applyType: inflight?.payload?.mode || applyType };
          setPreviewResult(item);
          setSelectedResultId("preview");
          setStatus("");
          emitNotify({
            id: `prints_done_${Date.now()}`,
            kind: "success",
            title: "Генерация готова",
            message: "Генерация завершилась после перезагрузки. Результат готов.",
            source: { studioKey: "prints" },
            ttlMs: 9000,
          });
          try { await refresh(); } catch(e) {}

          try { localStorage.removeItem(KEY_PRINTS_INFLIGHT); dbg("inflight:clear", { key: KEY_PRINTS_INFLIGHT }); } catch(e) {}
          dbg("inflight:resume_done", { ok: true });
        } catch (e) {
          dbg("inflight:resume_err", { message: String(e?.message || e) });
          log(String(e?.message || e || "Ошибка resume"), "error");
          // keep inflight so user can reload and retry
        } finally {
          setBusy(false);
          setBusyPhrase("");
          try { if (busyTimerRef.current) clearInterval(busyTimerRef.current); } catch(e) {}
          busyTimerRef.current = null;
        }
      })();
    } catch (e) {
      // ignore
    }
  }, [authLoading, busy, KEY_PRINTS_INFLIGHT, fetchJson, dbg]);

const canEditMarker = !!mainImageUrl;

  // Convert image-relative bbox -> stage pixel rect for rendering the marker inside the real image box.
  const markerPx = React.useMemo(() => {
    if (!canEditMarker) return null;
    const box = getImageBox();
    if (!box) return null;
    const { offX, offY, contentW, contentH } = box;
    const left = offX + bbox.x * contentW;
    const top = offY + bbox.y * contentH;
    const width = bbox.w * contentW;
    const height = bbox.h * contentH;
    return { left, top, width, height };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canEditMarker, bbox.x, bbox.y, bbox.w, bbox.h, rotationDeg, mainImageUrl, stageTick]);

  
  // Render curved preview inside marker (frontend-only preview; backend placement remains bbox + rotation).
  React.useEffect(() => {
    const canvas = pinCanvasRef.current;
    if (!canvas) return;
    const url = resolveUrl(markerLogoUrl);
    if (!url) return;

    const box = markerPx;
    const cssW = Math.max(32, Math.round(box?.width || 0));
    const cssH = Math.max(32, Math.round(box?.height || 0));
    if (!cssW || !cssH) return;

    const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    canvas.width = Math.round(cssW * dpr);
    canvas.height = Math.round(cssH * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const srcImg = _removeBgPreview(img); // canvas with transparent background
      const iw = srcImg.width || img.naturalWidth || 1;
      const ih = srcImg.height || img.naturalHeight || 1;
      const dw = cssW;
      const dh = cssH;

      // Warp is 4 independent bends. We combine opposite sides into axis curves
      // (same as backend _arc_warp_design): curve_v = bottom - top, curve_h = right - left.
      const w = markerWarp || { top: 0, right: 0, bottom: markerCurve || 0, left: 0 };
      const top = clamp(Number(w.top || 0), -1, 1);
      const right = clamp(Number(w.right || 0), -1, 1);
      const bottom = clamp(Number(w.bottom ?? markerCurve ?? 0), -1, 1);
      const left = clamp(Number(w.left || 0), -1, 1);

      const curveV = clamp((bottom - top), -1, 1);
      const curveH = clamp((right - left), -1, 1);

      // temp canvas with stretched image (FILL, like generation)
      const dpr2 = 1;
      const tmp0 = document.createElement("canvas");
      tmp0.width = dw * dpr2;
      tmp0.height = dh * dpr2;
      const c0 = tmp0.getContext("2d");
      if (!c0) return;
      c0.setTransform(dpr2, 0, 0, dpr2, 0, 0);
      c0.clearRect(0, 0, dw, dh);
      // Auto-fit: keep full warped design visible inside marker box (no clipping at edges).
// Warp shifts can push pixels outside the bbox; we pre-scale the source into an inner safe area.
const needV = Math.abs(curveV) * 0.28; // max vertical shift fraction of H
const needH = Math.abs(curveH) * 0.22; // max horizontal shift fraction of W
const baseInset = 0.04; // always keep a small breathing room
const inset = clamp(baseInset + Math.max(needV, needH), 0, 0.35);
const fitScale = clamp(1 - inset * 2, 0.1, 1);

const srcW = dw * fitScale;
const srcH = dh * fitScale;
const srcX = (dw - srcW) / 2;
const srcY = (dh - srcH) / 2;

c0.drawImage(srcImg, 0, 0, iw, ih, srcX, srcY, srcW, srcH);

      // PASS 1: vertical arc (shift columns)
      const tmp1 = document.createElement("canvas");
      tmp1.width = dw * dpr2;
      tmp1.height = dh * dpr2;
      const c1 = tmp1.getContext("2d");
      if (!c1) return;
      c1.setTransform(dpr2, 0, 0, dpr2, 0, 0);
      c1.clearRect(0, 0, dw, dh);
      const ampV = curveV * (dh * 0.28);
      const stepX = 2;
      for (let x = 0; x < dw; x += stepX) {
        const x2 = Math.min(dw, x + stepX);
        const mid = x + (x2 - x) * 0.5;
        const t = (mid - dw / 2) / (dw / 2);
        const wv = 1 - t * t;
        const yShift = Math.round(-ampV * wv);
        c1.drawImage(tmp0, x, 0, (x2 - x), dh, x, yShift, (x2 - x), dh);
      }

      // PASS 2: horizontal arc (shift rows) into final canvas
      ctx.clearRect(0, 0, dw, dh);
      ctx.globalAlpha = 0.99;
      const ampH = curveH * (dw * 0.22);
      const stepY = 2;
      for (let y = 0; y < dh; y += stepY) {
        const y2 = Math.min(dh, y + stepY);
        const mid = y + (y2 - y) * 0.5;
        const t = (mid - dh / 2) / (dh / 2);
        const wv = 1 - t * t;
        const xShift = Math.round(-ampH * wv);
        ctx.drawImage(tmp1, 0, y, dw, (y2 - y), xShift, y, dw, (y2 - y));
      }
      ctx.globalAlpha = 1;
    };
    img.onerror = () => {
      // fallback: clear on error
      ctx.clearRect(0, 0, cssW, cssH);
    };
    img.src = url;
  }, [markerLogoUrl, markerCurve, markerWarp, markerPx?.width, markerPx?.height]);

return (
    <div className="printsV3">
      <div className="printsTop">
        <div className="title">Принты и дизайн</div>
        <div className="subtitle">Одежда → Лого → Метка → Вид нанесения → Результат</div>
      </div>

      <div className="printsGrid">
        {/* LEFT: inputs */}
        <div className="panel left">
          <div className="panelTitle">Вход</div>

          <div className="block">
            <div className="blockLabel">Фото одежды</div>
            <div className="btnRow">
              <button className="btn" onClick={() => pickFile((u) => addGarment(u))} disabled={busy}>Файл</button>
              <button className="btn" onClick={() => pickUrl((u) => addGarment(u))} disabled={busy}>URL</button>
              <button className="btn ghost" onClick={clearAllGarments} disabled={busy || garmentSlots.every((x) => !x)}>Очистить</button>
            </div>

            <div className="framesBlock">
              <div className="framesStrip" role="list" aria-label="Кадры одежды">
              {garmentSlots.map((u, idx) => {
                const active = idx === activeGarmentIdx;
                return (
                  <button
                    type="button"
                    key={idx}
                    className={"frameCard" + (active ? " active" : "")}
                    onClick={() => {
                      setActiveGarmentIdx(idx);
                      setSelectedResultId(null);
                      setBaseLocked(false);
                      setBaseUrl(null);
                    }}
                    disabled={busy}
                    title={u ? `Кадр ${idx + 1}` : `Пусто (Кадр ${idx + 1})`}
                  >

                    <div className="frameNum">{idx + 1}</div>
                    {u ? (
                      <div
                        className="frameDel"
                        role="button"
                        aria-label={`Удалить кадр ${idx + 1}`}
                        title="Удалить"
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); deleteGarment(idx); }}
                      >
                        ×
                      </div>
                    ) : null}

                    <div className="frameThumb">

                      {u ? (
                          <>
                            <img src={resolveUrl(u)} alt="" />
                          </>
                        ) : (
                          <div className="frameEmpty" />
                        )}
                    </div>
                  </button>
                );
              })}
            
              </div>
              <div className="framesScrollHint">Прокрути, чтобы увидеть кадры 4–9</div>
            </div>
          </div>

<div className="block">
            <div className="blockLabel">Логотип</div>
            <div className="btnRow">
              <button className="btn" onClick={() => pickFile((u) => addLogo(u))} disabled={busy}>Файл</button>
              <button className="btn" onClick={() => pickUrl((u) => addLogo(u), "URL логотипа")} disabled={busy}>URL</button>
              <button className="btn ghost" onClick={clearAllLogos} disabled={busy || logoSlots.every((x) => !x)}>Очистить</button>
            </div>

            <div className="framesBlock logoFrames">
              <div className="framesStrip" role="list" aria-label="Логотипы (слоты 1–9)">
                {logoSlots.map((u, idx) => {
                  const active = idx === activeLogoIdx;
                  return (
                    <button
                      type="button"
                      key={idx}
                      className={"frameCard" + (active ? " active" : "")}
                      onClick={() => setActiveLogoIdx(idx)}
                      disabled={busy}
                      title={u ? `Лого ${idx + 1}` : `Пусто (Лого ${idx + 1})`}
                    >
                      <div className="frameNum">{idx + 1}</div>
                      {u ? (
                        <div
                          className="frameDel onImg"
                          role="button"
                          aria-label={`Удалить лого ${idx + 1}`}
                          title="Удалить"
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); deleteLogo(idx); }}
                        >
                          ×
                        </div>
                      ) : null}
                      <div className="frameThumb">
                        {u ? (
                          <>
                            <img src={resolveUrl(u)} alt="" />
                          </>
                        ) : (
                          <div className="frameEmpty" />
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
              <div className="framesScrollHint">Прокрути, чтобы увидеть лого 4–9</div>
            </div>

            <div className="logoSlot">
              {logoUrl ? <img src={resolveUrl(logoUrl)} alt="" style={{ display: "none" }} /> : <div className="logoEmpty">Нет логотипа</div>}
            </div>
          </div>

          <div className="hint">
            {selectedResult
              ? "Сейчас открыт результат. Нажми «База», чтобы снова ставить метку."
              : (garmentUrl ? "Поставь метку на фото (клик+drag). Можно двигать метку мышью." : "Добавь фото одежды, чтобы начать.")
            }
          </div>
        </div>

        {/* CENTER: main stage */}
        <div className="panel center">
          <div className="panelTitle">
            <span>Поле разметки</span>
          </div>

          <div
            className={"stage " + (canEditMarker ? "editable" : "locked")}
            ref={stageRef}
            onMouseDown={onStageDown}
            onMouseMove={onStageMove}
            onMouseUp={onStageUp}
            onMouseLeave={onStageUp}
            onWheel={onStageWheel}
          >
            {!mainImageUrl ? (
              <div className="stageEmpty">Добавь фото одежды слева</div>
            ) : (
              <>
                <img className="stageImg" src={resolveUrl(mainImageUrl)} alt=""  onLoad={() => setStageTick((t) => t + 1)} />

                {/* marker only on base */}
                {canEditMarker && (
                  <div
                    className="markerRect"
                    style={{
                      left: `${(markerPx?.left || 0).toFixed(2)}px`,
                      top: `${(markerPx?.top || 0).toFixed(2)}px`,
                      width: `${(markerPx?.width || 0).toFixed(2)}px`,
                      height: `${(markerPx?.height || 0).toFixed(2)}px`,
                      transform: `rotate(${rotationDeg}deg)`,
                      transformOrigin: "center center",
                    }}
                    onPointerDown={(e) => beginPinDrag(e, "move")}
                    title="Перемести рамку мышью (внутри). Ручки: ↘︎ размер, ↕︎ высота, ⌒ дуга (тяни вверх/вниз)."
                  >
                    <div
                      className="pinInner"
                    >
                      <div
                        className="markerSurface"
                        style={{
                          transform: `none`,
                          transformOrigin: "center center",
                        }}
                      >
                      {markerLogoUrl ? (
                        <canvas className="pinPreviewCanvas" ref={pinCanvasRef} />
                      ) : (
                        <div className="pinPlaceholder">ЛОГО</div>
                      )}

                      <div className="pinGuides" />

                                            </div>

                      {/* handles */}
                      <div
                        className="pinHandle pinHandleBendTop"
                        onPointerDown={(e) => beginPinDrag(e, "bendTop")}
                        title="Гнуть верх (тяни вверх/вниз)"
                      />
                      <div
                        className="pinHandle pinHandleBendLeft"
                        onPointerDown={(e) => beginPinDrag(e, "bendLeft")}
                        title="Гнуть левый край (тяни влево/вправо)"
                      />
                      <div
                        className="pinHandle pinHandleBendRight"
                        onPointerDown={(e) => beginPinDrag(e, "bendRight")}
                        title="Гнуть правый край (тяни влево/вправо)"
                      />
                      <div
                        className="pinHandle pinHandleScale"
                        onPointerDown={(e) => beginPinDrag(e, "scale")}
                        title="Увеличить/уменьшить (по диагонали)"
                      />
                      <div
                        className="pinHandle pinHandleBend"
                        onPointerDown={(e) => beginPinDrag(e, "bendBottom")}
                        title="Гнуть низ (тяни вверх/вниз)"
                      />
</div>

                  </div>
                )}

                {/* busy overlay: blocks interactions while generating */}
                {busy && (
                  <div className="stageBusyOverlay" role="status" aria-live="polite">
                    <div className="stageBusyText">{busyPhrase || "Наношу…"}</div>
                    <div className="stageBusySub">Подожди чуть-чуть — магия печати в процессе</div>
                  </div>
                )}

                {/* small coords */}
                {canEditMarker && (
                  <div className="coords">
                    x:{bbox.x.toFixed(3)} y:{bbox.y.toFixed(3)} w:{bbox.w.toFixed(3)} h:{bbox.h.toFixed(3)}
                  </div>
                )}
              </>
            )}
          </div>

          <div className="bottomBar">
            <button className="primary" onClick={onGenerate} disabled={busy || !mainImageUrl || !(logoSlots[activeLogoIdx] || logoUrl)}>
              {busy ? "Генерирую..." : "Сгенерировать"}
            </button>
          </div>

          <div className="statusChat" aria-live="polite">
            <div className="statusChatHeader">Статус</div>
            <div className="statusChatBody">
              {logItems.length ? (
                logItems.map((m) => (
                  <div key={m.id} className={"statusMsg " + (m.kind || "info")}>
                    <div className="statusText">{m.text}</div>
                    <div className="statusTime">{new Date(m.ts).toLocaleTimeString()}</div>
                  </div>
                ))
              ) : (
                <div className="statusEmpty">Пока нет событий</div>
              )}
            </div>
          </div>
        </div>

        {/* RIGHT: controls + mini result + gallery */}
        <div className="panel right">
          <div className="panelTitle">Настройки</div>

          
          <div className="block">
            <div className="blockLabel">Вид нанесения</div>

            <ApplyTypeSelect
              value={applyType}
              onChange={setApplyType}
              items={APPLY_TYPES}
              disabled={busy}
            />

            <div className={`rotBlock ${rotHot ? "hot" : ""}`}>
            <div className="rotRow">
              <div className="rotLabel">Поворот лого</div>
              <div className="rotControls">
                
                <button className="btn tiny ghost" onClick={() => setRotationDeg(0)} disabled={busy} title="Сброс на 0°">0°</button>
<button className="btn tiny" onClick={() => setRotationDeg((d) => (d + 345) % 360)} disabled={busy}>↺ 15°</button>
                <button className="btn tiny" onClick={() => setRotationDeg((d) => (d + 15) % 360)} disabled={busy}>↻ 15°</button>
              </div>
            </div>

                        <div className="rotDialWrap" aria-label="Поворот лого">
              <div className="rotDialTrack">
                <div className="rotDialCenter" title="0° (центр)">0°</div>
                <input
                  className="rotDial"
                  type="range"
                  min="-180"
                  max="180"
                  value={rotToDelta(rotationDeg)}
                  onChange={(e) => setRotationDeg(deltaToRot(e.target.value))}
                  disabled={busy}
                />
              </div>
              <div className="rotDialReadout">
                <button type="button" className={`rotArrowBtn rotArrowBtn--neg ${rotArrowFlash === "neg" ? "isActive" : ""}`} onClick={() => { if (busy) return; setRotationDeg((d) => (d + 359) % 360); setRotArrowFlash("neg"); window.setTimeout(() => setRotArrowFlash(""), 160); }} disabled={busy} aria-label="Повернуть на -1°">←</button>
                <span className="rotDialVal">{rotToDelta(rotationDeg)}°</span>
                <button type="button" className={`rotArrowBtn rotArrowBtn--pos ${rotArrowFlash === "pos" ? "isActive" : ""}`} onClick={() => { if (busy) return; setRotationDeg((d) => (d + 1) % 360); setRotArrowFlash("pos"); window.setTimeout(() => setRotArrowFlash(""), 160); }} disabled={busy} aria-label="Повернуть на +1°">→</button>
              </div>
            </div>
            <div className="rotHint">
              {selectedResult ? "Чтобы крутить метку — нажми «База»." : "Shift + колесо над меткой тоже поворачивает."}
            </div>
          </div>
          </div>


          <div className="block resultBlock">
            <div className="blockLabel">Результат</div>
            <div className="miniResult">
              {selectedResult ? (
                <img src={resolveUrl(selectedResult.url)} alt="" onClick={() => setLightboxUrl(resolveUrl(selectedResult.url))} />
              ) : (
                <div className="miniEmpty">Сгенерируй, и тут появится превью</div>
              )}
            </div>
            <div className="miniBtns">
              <button
                className="btn"
                onClick={() => {
                  if (!selectedResult) return;
                  // Add to избранное and make it new base
                  const url = selectedResult.url;
                  const item = { id: `${Date.now()}_${Math.random().toString(16).slice(2)}`, url, ts: Date.now(), applyType: selectedResult.applyType || applyType };
                  setResults((arr) => [item, ...arr].slice(0, 9));
                  setBaseUrl(url);
                  setBaseLocked(true);
                  setBbox({ x: 0.35, y: 0.32, w: 0.28, h: 0.18 });
                  setRotationDeg(0);
                  setSelectedResultId(item.id); // keep showing in Result
                  setPreviewResult(null);
                }}
                disabled={!selectedResult || busy}
                title="Оставить результат как новую базу и продолжить нанесение"
              >
                Оставить
              </button>

              <button
                className="btn danger"
                onClick={() => { if (!selectedResult || busy) return; setSelectedResultId(null); setPreviewResult(null); }}
                disabled={!selectedResult || busy}
                title="Убрать результат (не удаляет файл на диске)"
              >
                Удалить
              </button>

              <button
                className="btn"
                onClick={async () => {
                  try{
                    if (!selectedResult) return;
                    await downloadAsPng(selectedResult.url, "result.png");
                  }catch(e){
                    console.error(e);
                    alert("Ошибка скачивания");
                  }
                }}
                disabled={!selectedResult || busy}
                title="Сохранить как PNG"
              >
                Сохранить (PNG)
              </button>
            </div>
          </div>

                    <div className="block">
            <div className="blockLabel">Избранное</div>

            <div className="framesBlock historyFrames">
              <div className="framesStrip" role="list" aria-label="Избранное (слоты 1–9)">
                {Array.from({ length: 9 }).map((_, idx) => {
                  const r = results[idx] || null;
                  const active = idx === activeHistoryIdx;
                  return (
                    <button
                      type="button"
                      key={idx}
                      className={"frameCard" + (active ? " active" : "")}
                      onClick={() => {
                        setActiveHistoryIdx(idx);
                        if (!r) return;
                        // Clicking a favourite makes it the current BASE so user can continue editing
                        setBaseUrl(r.url);
                        setBaseLocked(true);
                        setSelectedResultId(r.id);
                      }}
                      disabled={busy}
                      title={r ? (APPLY_TYPES.find(x => x.key===r.applyType)?.label || r.applyType) : `Пусто (Избранное ${idx + 1})`}
                    >
                      <div className="frameNum">{idx + 1}</div>
                      {r ? (
                        <div
                          className="frameDel onImg"
                          role="button"
                          aria-label={`Удалить из истории ${idx + 1}`}
                          title="Удалить"
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); deleteResultById(r.id); }}
                        >
                          ×
                        </div>
                      ) : null}
                      <div className="frameThumb">
                        {r ? (
                          <>
                            <img src={resolveUrl(r.url)} alt="" />
                          </>
                        ) : (
                          <div className="frameEmpty" />
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
              <div className="framesScrollHint">Прокрути, чтобы увидеть избранное 4–9</div>
            </div>

            <div className="historyActions">
              <button
                className="btn"
                disabled={!results[activeHistoryIdx] || busy}
                onClick={async () => {
                  try{
                    const urls = results.filter(Boolean).map((r) => r.url);
                    if (urls.length === 0) return;
                    if (urls.length === 1) {
                      await downloadAsPng(urls[0], "favorite.png");
                    } else {
                      await downloadManyAsZip(urls, "favorites.zip");
                    }
                  }catch(e){
                    console.error(e);
                    // If jszip isn't installed, show a clear hint
                    const msg = String(e && (e.message || e));
                    if (msg.toLowerCase().includes("jszip") || msg.toLowerCase().includes("cannot find module")) {
                      alert("Для ZIP нужен пакет jszip. Установи: npm i jszip");
                    } else {
                      alert("Ошибка скачивания");
                    }
                  }
                }} title="Сохранить выбранный результат"
              >
                Сохранить
              </button>

              <button
                className="btn"
                disabled
                onClick={() => {}}
                title="Пока отключено (в этой версии кнопка не используется)"
              >
                Перейти
              </button>
            </div>
          </div>

          <div className="hint small">
            Логика простая: метку ставим только на базе. Результаты смотри через историю и кнопки “База/Удалить”.
          </div>
        </div>
      </div>

      {/* URL modal (portal) */}
      {urlModal.open && ReactDOM.createPortal(
        <div className="ps-modalOverlay" onClick={closeUrlModal}>
          <div className="ps-urlModal" onClick={(e) => e.stopPropagation()}>
            <div className="ps-urlTitle">{urlModal.title || "URL изображения"}</div>
            <input
              className="ps-urlInput"
              placeholder="https://... или /static/assets/..."
              value={urlModal.value}
              onChange={(e) => setUrlModal((p) => ({ ...p, value: e.target.value, error: "" }))}
              onKeyDown={(e) => {
                if (e.key === "Escape") closeUrlModal();
                if (e.key === "Enter") confirmUrlModal();
              }}
              autoFocus
            />
            {urlModal.error ? <div className="ps-urlError">{urlModal.error}</div> : null}
            <div className="ps-urlActions">
              <button className="btn ghost" type="button" onClick={closeUrlModal}>Отмена</button>
              <button className="btn" type="button" onClick={confirmUrlModal}>Готово</button>
            </div>
          </div>
        </div>,
        document.body
      )}

      {lightboxUrl ? (
        <div className="psLightboxOverlay" onClick={() => setLightboxUrl("")}>
          <img className="psLightboxImg" src={lightboxUrl} alt="" />
        </div>
      ) : null}
    </div>

  );
}