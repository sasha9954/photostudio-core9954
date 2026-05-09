export function persistLastAuthUserIdentity(user) {
  if (!user || typeof user !== "object") return false;

  try {
    localStorage.setItem("ps:lastUserId", String(user.id || user.user_id || ""));
    localStorage.setItem("ps:lastEmail", String(user.email || "").toLowerCase());
    return true;
  } catch {
    return false;
  }
}
