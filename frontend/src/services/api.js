// IMPORTANT: cookie-session with SameSite=Lax won't work reliably if frontend and backend
// are on different "sites" (e.g. localhost vs 127.0.0.1). Поэтому подстраиваемся под
// текущий hostname, чтобы API_BASE совпадал со "сайтом" фронта.

function normalizeApiErrorMessage(res, data){
  // FastAPI validation: {detail: [{loc: [...], msg: "...", type: "..."}]}
  const d = data?.detail ?? data?.message ?? data?.error ?? null;
  const hint = typeof data?.hint === "string" ? data.hint.trim() : "";
  const code = typeof data?.code === "string" ? data.code.trim() : "";
  if (hint) return code ? `${hint} (${code})` : hint;
  if(Array.isArray(d)){
    const parts = d.map(it=>{
      if(typeof it === "string") return it;
      const loc = Array.isArray(it?.loc) ? it.loc.join(".") : (it?.loc ?? "");
      const msg = it?.msg ?? JSON.stringify(it);
      return loc ? `${loc}: ${msg}` : msg;
    });
    const base = parts.join(" | ");
    return code ? `${base} (${code})` : base;
  }
  if(typeof d === "string") return code ? `${d} (${code})` : d;
  if(d && typeof d === "object") {
    const base = d.message || JSON.stringify(d);
    return code ? `${base} (${code})` : base;
  }
  return code ? `HTTP ${res.status} (${code})` : `HTTP ${res.status}`;
}

function createApiError({
  message = "Request failed",
  status = null,
  code = "",
  hint = "",
  payload = null,
  path = "",
  method = "GET",
} = {}){
  const error = new Error(String(message || "Request failed"));
  error.name = "ApiError";
  error.status = Number.isFinite(Number(status)) ? Number(status) : null;
  error.code = String(code || "").trim();
  error.hint = String(hint || "").trim();
  error.payload = payload ?? null;
  error.path = String(path || "");
  error.method = String(method || "GET");
  return error;
}

export const API_BASE = `http://${window.location.hostname}:8000`;
export async function fetchJson(path,{method="GET",headers={},body,signal,timeoutMs=0}={}){
  const timeoutValue = Number(timeoutMs);
  const hasTimeout = Number.isFinite(timeoutValue) && timeoutValue > 0;
  const timeoutController = hasTimeout ? new AbortController() : null;
  const timeoutSignal = timeoutController?.signal;
  const activeSignal = timeoutSignal || signal;
  let didTimeout = false;
  let timeoutId = null;
  let signalAbortHandler = null;

  if (timeoutController && signal) {
    if (signal.aborted) {
      timeoutController.abort(signal.reason);
    } else {
      signalAbortHandler = () => timeoutController.abort(signal.reason);
      signal.addEventListener("abort", signalAbortHandler, { once: true });
    }
  }

  if (timeoutController && hasTimeout) {
    timeoutId = window.setTimeout(() => {
      didTimeout = true;
      timeoutController.abort(new Error(`timeout:${timeoutValue}`));
    }, timeoutValue);
  }

  try {
    const res = await fetch(`${API_BASE}${path}`,{
      credentials: "include",
      method,
      signal: activeSignal,
      headers: {"Content-Type":"application/json",...headers},
      body: body?JSON.stringify(body):undefined
    });
    const text = await res.text();
    let data=null;
    try{ data = text?JSON.parse(text):null; }catch{ data={raw:text}; }
    if(!res.ok){
      // FastAPI часто возвращает {detail: ...}
      const msg = normalizeApiErrorMessage(res, data);
      throw createApiError({
        message: msg,
        status: res.status,
        code: data?.code,
        hint: data?.hint,
        payload: data,
        path,
        method,
      });
    }
    return data;
  } catch (error) {
    const isTimeoutAbort = hasTimeout && didTimeout;
    if (isTimeoutAbort) {
      throw createApiError({
        message: `Request timeout after ${timeoutValue}ms (${method} ${path})`,
        status: 0,
        code: "REQUEST_TIMEOUT",
        hint: "Попробуйте повторить запрос",
        payload: null,
        path,
        method,
      });
    }
    if (error && typeof error === "object" && "status" in error && "payload" in error) throw error;
    throw createApiError({
      message: String(error?.message || error || "Request failed"),
      status: Number.isFinite(Number(error?.status)) ? Number(error.status) : null,
      code: error?.code,
      hint: error?.hint,
      payload: error?.payload ?? null,
      path,
      method,
    });
  } finally {
    if (timeoutId) window.clearTimeout(timeoutId);
    if (signal && signalAbortHandler) signal.removeEventListener("abort", signalAbortHandler);
  }
}
export async function health(){ return fetchJson("/api/health"); }
