// IMPORTANT: cookie-session with SameSite=Lax won't work reliably if frontend and backend
// are on different "sites" (e.g. localhost vs 127.0.0.1). Поэтому подстраиваемся под
// текущий hostname, чтобы API_BASE совпадал со "сайтом" фронта.

function formatApiError(res, data){
  // FastAPI validation: {detail: [{loc: [...], msg: "...", type: "..."}]}
  const d = data?.detail ?? data?.message ?? data?.error ?? null;
  if(Array.isArray(d)){
    const parts = d.map(it=>{
      if(typeof it === "string") return it;
      const loc = Array.isArray(it?.loc) ? it.loc.join(".") : (it?.loc ?? "");
      const msg = it?.msg ?? JSON.stringify(it);
      return loc ? `${loc}: ${msg}` : msg;
    });
    return parts.join(" | ");
  }
  if(typeof d === "string") return d;
  if(d && typeof d === "object") return d.message || JSON.stringify(d);
  return `HTTP ${res.status}`;
}

export const API_BASE = `http://${window.location.hostname}:8000`;
export async function fetchJson(path,{method="GET",headers={},body,signal,timeoutMs=0}={}){
  const timeoutValue = Number(timeoutMs);
  const hasTimeout = Number.isFinite(timeoutValue) && timeoutValue > 0;
  const timeoutController = hasTimeout ? new AbortController() : null;
  const timeoutSignal = timeoutController?.signal;
  const activeSignal = timeoutSignal || signal;
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
      const msg = formatApiError(res, data);
      throw new Error(msg);
    }
    return data;
  } catch (error) {
    const isTimeoutAbort = hasTimeout && (timeoutSignal?.aborted || false);
    if (isTimeoutAbort) {
      throw new Error(`Request timeout after ${timeoutValue}ms (${method} ${path})`);
    }
    throw error;
  } finally {
    if (timeoutId) window.clearTimeout(timeoutId);
    if (signal && signalAbortHandler) signal.removeEventListener("abort", signalAbortHandler);
  }
}
export async function health(){ return fetchJson("/api/health"); }
