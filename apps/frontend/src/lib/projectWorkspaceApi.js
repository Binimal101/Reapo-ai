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

export async function createChatSession(token) {
  const data = await apiFetch("/chat/sessions", withBearer(token, { method: "POST", body: "{}" }));
  return data?.session ?? null;
}

export async function getChatSession(token, sessionId) {
  const data = await apiFetch(
    `/chat/sessions/${encodeURIComponent(sessionId)}`,
    withBearer(token, { method: "GET" })
  );
  return data?.session ?? null;
}

export async function getChatRun(token, runId) {
  const data = await apiFetch(`/chat/runs/${encodeURIComponent(runId)}`, withBearer(token, { method: "GET" }));
  return data?.run ?? null;
}

export async function sendChatMessage(token, body) {
  return apiFetch(
    "/chat/messages",
    withBearer(token, {
      method: "POST",
      body: JSON.stringify(body),
    })
  );
}

export async function pullTraceSnapshot(token, traceId) {
  const q = new URLSearchParams({ trace_id: traceId, limit: "80" });
  const data = await apiFetch(`/observability/trace-stack?${q.toString()}`, withBearer(token, { method: "GET" }));
  return data;
}

export async function listProjectRepositories(token, projectId) {
  const data = await apiFetch(
    `/projects/${encodeURIComponent(projectId)}/repositories`,
    withBearer(token, { method: "GET" })
  );
  return Array.isArray(data?.repositories) ? data.repositories : [];
}

export async function writeWorkspaceEvent(token, { name, trace_id: traceId, input, output }) {
  await apiFetch(
    "/observability/events",
    withBearer(token, {
      method: "POST",
      body: JSON.stringify({
        name,
        trace_id: traceId,
        input,
        output,
      }),
    })
  );
}
