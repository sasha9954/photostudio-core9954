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
export async function fetchJson(path,{method="GET",headers={},body,signal}={}){
  const res = await fetch(`${API_BASE}${path}`,{
    credentials: "include",
    method,
    signal,
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
}
export async function health(){ return fetchJson("/api/health"); }
