const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

async function readJsonSafely(response) {
  const raw = await response.text();
  if (!raw || !raw.trim()) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return { status: "error", reason: raw };
  }
}

export function getStoredSessionToken() {
  return localStorage.getItem("reapo_session_token");
}

export function storeSessionToken(token) {
  localStorage.setItem("reapo_session_token", token);
}

export function clearSessionToken() {
  localStorage.removeItem("reapo_session_token");
}

export async function startOAuthFlow({ flow, redirectUri }) {
  const endpoint = flow === "signup" ? "/auth/oauth/signup/start" : "/auth/oauth/signin/start";
  const state = `reapo-${flow}-${crypto.randomUUID()}`;
  const response = await fetch(apiUrl(endpoint), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      provider: "github",
      state,
      redirect_uri: redirectUri,
    }),
  });

  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to start OAuth flow");
  }

  if (!payload) {
    throw new Error("OAuth start returned an empty response");
  }

  return {
    authorizeUrl: payload.authorize_url,
    state: payload.state,
    flow: payload.flow,
  };
}

export async function finishOAuthCallback({ flow, code, state, redirectUri }) {
  const response = await fetch(apiUrl("/auth/oauth/callback"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      provider: "github",
      flow,
      code,
      state,
      redirect_uri: redirectUri,
    }),
  });

  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "OAuth callback failed");
  }

  if (!payload) {
    throw new Error("OAuth callback returned an empty response");
  }

  return payload;
}

export async function validateSessionToken(token) {
  const response = await fetch(apiUrl("/auth/session/validate"), {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
    },
  });

  if (!response.ok) {
    return null;
  }

  const payload = await readJsonSafely(response);
  if (!payload) {
    return null;
  }
  return payload.user_id || null;
}

function authHeaders(token, withJson = false) {
  const headers = {
    authorization: `Bearer ${token}`,
  };
  if (withJson) {
    headers["content-type"] = "application/json";
  }
  return headers;
}

export async function listUserProjects(token) {
  const response = await fetch(apiUrl("/projects"), {
    method: "GET",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to list projects");
  }
  return Array.isArray(payload?.projects) ? payload.projects : [];
}

export async function createProject(token, name, description) {
  const response = await fetch(apiUrl("/projects"), {
    method: "POST",
    headers: authHeaders(token, true),
    body: JSON.stringify({ name, description }),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to create project");
  }
  return payload?.project;
}

export async function updateProject(token, projectId, name, description) {
  const response = await fetch(apiUrl(`/projects/${projectId}`), {
    method: "PATCH",
    headers: authHeaders(token, true),
    body: JSON.stringify({ name, description }),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to update project");
  }
  return payload?.project;
}

export async function deleteProject(token, projectId) {
  const response = await fetch(apiUrl(`/projects/${projectId}`), {
    method: "DELETE",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to delete project");
  }
  return payload;
}

export async function listGithubUserRepositories(token) {
  const response = await fetch(apiUrl("/auth/github/user-repos?per_page=100"), {
    method: "GET",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to list GitHub repositories");
  }
  return Array.isArray(payload?.repositories) ? payload.repositories : [];
}

export async function addRepositoryToProject(token, projectId, repo) {
  const response = await fetch(apiUrl(`/projects/${projectId}/repositories`), {
    method: "POST",
    headers: authHeaders(token, true),
    body: JSON.stringify({
      id: repo.id,
      owner: repo.owner,
      name: repo.name,
      visibility: repo.visibility,
    }),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to add repository to project");
  }
  return payload;
}

export async function listProjectRepositories(token, projectId) {
  const response = await fetch(apiUrl(`/projects/${projectId}/repositories`), {
    method: "GET",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to list project repositories");
  }
  return Array.isArray(payload?.repositories) ? payload.repositories : [];
}

export async function removeRepositoryFromProject(token, projectId, repositoryId) {
  const response = await fetch(apiUrl(`/projects/${projectId}/repositories/${repositoryId}`), {
    method: "DELETE",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to remove repository from project");
  }
  return payload;
}
