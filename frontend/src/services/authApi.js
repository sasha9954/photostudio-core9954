import { fetchJson } from "./api.js";

export async function authMe(){
  try{ return await fetchJson("/api/auth/me"); }catch{ return { ok:false, user:null }; }
}
export async function authLogin({ email, password }){
  return fetchJson("/api/auth/login", { method:"POST", body:{ email, password } });
}
export async function authRegister({ email, password, name }){
  return fetchJson("/api/auth/register", { method:"POST", body:{ email, password, name } });
}
export async function authLogout(){
  return fetchJson("/api/auth/logout", { method:"POST" });
}

export async function creditsTopup({ amount, credits, ref, reason }) {
  return fetchJson("/api/credits/topup", { method:"POST", body:{ amount } });
}
export async function creditsLedger({ limit=50 }={}){
  return fetchJson(`/api/credits/ledger?limit=${encodeURIComponent(limit)}`);
}
export async function creditsSpend({ amount, reason = "SPEND", ref = "" }){
  // Важно: используем тот же клиент, что и остальной auth/credits API.
  // Раньше здесь был вызов apiPost (не определён) + неверный путь без /api.
  return fetchJson("/api/credits/spend", { method: "POST", body: { amount, reason, ref } });
}
