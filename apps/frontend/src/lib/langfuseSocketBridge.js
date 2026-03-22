import { io } from "socket.io-client";

const SOCKET_URL = import.meta.env.VITE_LANGFUSE_SOCKET_URL || "";

let socketInstance = null;
let connectAttempted = false;

function getSocket() {
  if (!SOCKET_URL) {
    return null;
  }
  if (socketInstance) {
    return socketInstance;
  }
  socketInstance = io(SOCKET_URL, {
    transports: ["websocket"],
    autoConnect: false,
    timeout: 1500,
  });
  return socketInstance;
}

function ensureConnected(socket) {
  if (!socket) {
    return false;
  }
  if (socket.connected) {
    return true;
  }
  if (!connectAttempted) {
    connectAttempted = true;
    socket.connect();
  }
  return socket.connected;
}

function emitWithAck(socket, event, payload, timeoutMs = 1600) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timeoutId = window.setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("socket_timeout"));
    }, timeoutMs);

    socket.emit(event, payload, (response) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeoutId);
      resolve(response);
    });
  });
}

export async function pullLangfuseTraceSnapshot({ token, traceId, fallback }) {
  const socket = getSocket();
  if (!socket || !ensureConnected(socket)) {
    return fallback();
  }
  try {
    const response = await emitWithAck(socket, "langfuse:trace:stack:pull", {
      token,
      trace_id: traceId,
    });
    if (!response || response.status === "error") {
      throw new Error(response?.reason || "socket_pull_failed");
    }
    return response;
  } catch {
    return fallback();
  }
}

export async function writeLangfuseEvent({ token, name, traceId, input, output, fallback }) {
  const socket = getSocket();
  if (!socket || !ensureConnected(socket)) {
    return fallback();
  }
  try {
    const response = await emitWithAck(socket, "langfuse:event:write", {
      token,
      name,
      trace_id: traceId,
      input,
      output,
    });
    if (!response || response.status === "error") {
      throw new Error(response?.reason || "socket_write_failed");
    }
    return response;
  } catch {
    return fallback();
  }
}
