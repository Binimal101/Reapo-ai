const SESSION_KEY = "reapo_session_token";

function apiBase() {
  const base = import.meta.env.VITE_API_BASE_URL;
  return typeof base === "string" && base.trim() ? base.replace(/\/$/, "") : "/api";
}

async function apiFetch(path, init = {}) {
  const url = `${apiBase()}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = {
    ...(init.body ? { "Content-Type": "application/json" } : {}),
    ...(init.headers || {}),
  };
  const res = await fetch(url, { ...init, headers });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    const reason = data?.reason || data?.error || res.statusText || "request_failed";
    throw new Error(typeof reason === "string" ? reason : JSON.stringify(reason));
  }
  return data;
}

function withBearer(token, init = {}) {
  return {
    ...init,
    headers: {
      ...init.headers,
      Authorization: `Bearer ${token}`,
    },
  };
}

export function getStoredSessionToken() {
  return localStorage.getItem(SESSION_KEY) || "";
}

export function storeSessionToken(token) {
  localStorage.setItem(SESSION_KEY, token);
}

export function clearSessionToken() {
  localStorage.removeItem(SESSION_KEY);
}

export async function validateSessionToken(token) {
  if (!token?.trim()) {
    return null;
  }
  const data = await apiFetch("/auth/session/validate", withBearer(token, { method: "POST", body: "{}" }));
  return typeof data?.user_id === "string" && data.user_id ? data.user_id : null;
}

export async function startOAuthFlow({ flow, redirectUri }) {
  const path = flow === "signup" ? "/auth/oauth/signup/start" : "/auth/oauth/signin/start";
  const data = await apiFetch(path, {
    method: "POST",
    body: JSON.stringify({
      provider: "github",
      redirect_uri: redirectUri,
      state: flow === "signup" ? "signup" : "signin",
    }),
  });
  const authorizeUrl = data?.authorize_url;
  if (typeof authorizeUrl !== "string" || !authorizeUrl) {
    throw new Error("missing_authorize_url");
  }
  return { authorizeUrl, state: data?.state };
}

export async function finishOAuthCallback({ flow, code, state, redirectUri }) {
  return apiFetch("/auth/oauth/callback", {
    method: "POST",
    body: JSON.stringify({
      provider: "github",
      flow,
      code,
      state,
      redirect_uri: redirectUri,
    }),
  });
}

export async function listUserProjects(token) {
  const data = await apiFetch("/projects", withBearer(token, { method: "GET" }));
  return Array.isArray(data?.projects) ? data.projects : [];
}

export async function listGithubUserRepositories(token) {
  const data = await apiFetch("/auth/github/user-repos?per_page=100", withBearer(token, { method: "GET" }));
  return Array.isArray(data?.repositories) ? data.repositories : [];
}

export async function createProject(token, name, description) {
  const data = await apiFetch(
    "/projects",
    withBearer(token, {
      method: "POST",
      body: JSON.stringify({ name, description: description ?? null }),
    })
  );
  return data?.project ?? data;
}

export async function updateProject(token, projectId, name, description) {
  const data = await apiFetch(
    `/projects/${encodeURIComponent(projectId)}`,
    withBearer(token, {
      method: "PATCH",
      body: JSON.stringify({ name, description: description ?? null }),
    })
  );
  return data?.project ?? data;
}

export async function deleteProject(token, projectId) {
  await apiFetch(`/projects/${encodeURIComponent(projectId)}`, withBearer(token, { method: "DELETE" }));
}

export async function listProjectRepositories(token, projectId) {
  const data = await apiFetch(
    `/projects/${encodeURIComponent(projectId)}/repositories`,
    withBearer(token, { method: "GET" })
  );
  return Array.isArray(data?.repositories) ? data.repositories : [];
}

export async function addRepositoryToProject(token, projectId, repo) {
  const body = {
    owner: repo?.owner,
    name: repo?.name,
    id: typeof repo?.id === "number" ? repo.id : undefined,
    visibility: repo?.visibility,
  };
  const data = await apiFetch(
    `/projects/${encodeURIComponent(projectId)}/repositories`,
    withBearer(token, {
      method: "POST",
      body: JSON.stringify(body),
    })
  );
  return data;
}

export async function removeRepositoryFromProject(token, projectId, repositoryId) {
  await apiFetch(
    `/projects/${encodeURIComponent(projectId)}/repositories/${encodeURIComponent(String(repositoryId))}`,
    withBearer(token, { method: "DELETE" })
  );
}
