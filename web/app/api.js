export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export const ROLES = [
  { id: "user", token: "dev-user-token" },
  { id: "reviewer", token: "dev-reviewer-token" },
  { id: "approver", token: "dev-approver-token" },
];

export function currentRole() {
  if (typeof window === "undefined") return ROLES[0];
  const token = localStorage.getItem("token") || ROLES[0].token;
  return ROLES.find((role) => role.token === token) || ROLES[0];
}

export async function api(path, options = {}) {
  const token = typeof window === "undefined" ? ROLES[0].token : localStorage.getItem("token") || ROLES[0].token;
  const headers = { Authorization: `Bearer ${token}`, ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`${API}${path}`, { ...options, headers });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const error = new Error(payload.error?.message || "Request failed");
    error.code = payload.error?.code;
    error.payload = payload;
    throw error;
  }
  return payload;
}
