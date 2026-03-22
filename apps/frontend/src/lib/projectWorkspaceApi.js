import { pullLangfuseTraceSnapshot, writeLangfuseEvent } from "./langfuseSocketBridge.js";

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

function authHeaders(token, withJson = false) {
  const headers = {
    authorization: `Bearer ${token}`,
  };
  if (withJson) {
    headers["content-type"] = "application/json";
  }
  return headers;
}

export async function createChatSession(token) {
  const response = await fetch(apiUrl("/chat/sessions"), {
    method: "POST",
    headers: authHeaders(token, true),
    body: JSON.stringify({}),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to create chat session");
  }
  return payload?.session;
}

export async function getChatSession(token, sessionId) {
  const response = await fetch(apiUrl(`/chat/sessions/${sessionId}`), {
    method: "GET",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to load chat session");
  }
  return payload?.session;
}

export async function getChatRun(token, runId) {
  const response = await fetch(apiUrl(`/chat/runs/${runId}`), {
    method: "GET",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to load run");
  }
  return payload?.run;
}

export async function getProject(token, projectId) {
  const response = await fetch(apiUrl(`/projects/${projectId}`), {
    method: "GET",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to load project");
  }
  return payload?.project;
}

export async function sendChatMessage(token, body) {
  const response = await fetch(apiUrl("/chat/messages"), {
    method: "POST",
    headers: authHeaders(token, true),
    body: JSON.stringify(body),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to send chat message");
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

async function fetchTraceSnapshot(token, traceId) {
  const query = traceId ? `?trace_id=${encodeURIComponent(traceId)}` : "";
  const response = await fetch(apiUrl(`/observability/trace-stack${query}`), {
    method: "GET",
    headers: authHeaders(token),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to pull trace snapshot");
  }
  return payload;
}

async function postLangfuseEvent(token, { name, traceId, input, output }) {
  const response = await fetch(apiUrl("/observability/events"), {
    method: "POST",
    headers: authHeaders(token, true),
    body: JSON.stringify({
      name,
      trace_id: traceId,
      input,
      output,
    }),
  });
  const payload = await readJsonSafely(response);
  if (!response.ok) {
    throw new Error(payload?.reason || "Failed to write observability event");
  }
  return payload;
}

export async function pullTraceSnapshot(token, traceId) {
  return pullLangfuseTraceSnapshot({
    token,
    traceId,
    fallback: () => fetchTraceSnapshot(token, traceId),
  });
}

export async function writeWorkspaceEvent(token, payload) {
  return writeLangfuseEvent({
    token,
    name: payload.name,
    traceId: payload.traceId,
    input: payload.input,
    output: payload.output,
    fallback: () => postLangfuseEvent(token, payload),
  });
}
