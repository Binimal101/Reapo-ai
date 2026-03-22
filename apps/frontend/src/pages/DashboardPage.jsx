import { useEffect, useState } from "react";
import Sidebar from "../components/Sidebar.jsx";
import CenterPanel from "../components/CenterPanel.jsx";
import RightPanel from "../components/RightPanel.jsx";
import {
  createChatSession,
  getChatRun,
  getChatSession,
  listProjectRepositories,
  pullTraceSnapshot,
  sendChatMessage,
  writeWorkspaceEvent,
} from "../lib/projectWorkspaceApi.js"; 

const PROJECT_SESSION_KEY = "reapo_project_sessions";

function readProjectSessions() {
  const raw = localStorage.getItem(PROJECT_SESSION_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeProjectSession(projectId, sessionId) {
  const current = readProjectSessions();
  current[projectId] = sessionId;
  localStorage.setItem(PROJECT_SESSION_KEY, JSON.stringify(current));
}

function lastRunIdFromMessages(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const runId = messages[index]?.run_id;
    if (typeof runId === "string" && runId) {
      return runId;
    }
  }
  return "";
}

function createDiffFromRun(run) {
  if (!run || typeof run.final_response !== "string" || !run.final_response.trim()) {
    return "";
  }
  const lines = run.final_response
    .split("\n")
    .slice(0, 24)
    .map((line) => (line.trim().startsWith("-") ? line : `+ ${line}`));
  return ["diff --git a/analysis.md b/analysis.md", "--- a/analysis.md", "+++ b/analysis.md", ...lines].join("\n");
}

function runHasAgenticTrace(run) {
  const steps = Array.isArray(run?.steps) ? run.steps : [];
  return steps.some((step) => {
    const name = typeof step?.name === "string" ? step.name : "";
    return (
      name === "execute_step.search"
      || name === "execute_step.grep_repo"
      || name.startsWith("execute_step.tool")
      || name.startsWith("execute_step.agent")
    );
  });
}

export default function DashboardPage({ project, sessionToken }) {
  const [selectedProjectId, setSelectedProjectId] = useState(() => project?.project_id || "");
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState([]);
  const [prompt, setPrompt] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [projectRepositories, setProjectRepositories] = useState([]);
  const [snapshot, setSnapshot] = useState(null);
  const [gitDiff, setGitDiff] = useState("");

  useEffect(() => {
    setSelectedProjectId(project?.project_id || "");
  }, [project?.project_id]);

  const hydrateFromRunId = async (runId) => {
    if (!runId || !sessionToken) {
      setGitDiff("");
      return;
    }
    try {
      const run = await getChatRun(sessionToken, runId);
      setGitDiff(createDiffFromRun(run));
      const traceId = typeof run?.trace_id === "string" ? run.trace_id : "";
      if (traceId && runHasAgenticTrace(run)) {
        const traceSnapshot = await pullTraceSnapshot(sessionToken, traceId);
        setSnapshot(traceSnapshot);
      } else {
        setSnapshot(null);
      }
    } catch {
      setSnapshot(null);
      setGitDiff("");
    }
  };

  useEffect(() => {
    if (!sessionToken || !selectedProjectId) {
      return;
    }

    setSessionId("");

    const sessionMap = readProjectSessions();
    const existingSessionId =
      typeof sessionMap[selectedProjectId] === "string" ? sessionMap[selectedProjectId] : "";

    const initializeSession = async () => {
      if (existingSessionId) {
        try {
          const existingSession = await getChatSession(sessionToken, existingSessionId);
          if (existingSession?.session_id) {
            setSessionId(existingSession.session_id);
            const restoredMessages = Array.isArray(existingSession.messages) ? existingSession.messages : [];
            setMessages(restoredMessages);
            await hydrateFromRunId(lastRunIdFromMessages(restoredMessages));
            return;
          }
        } catch {
          // Fall through to creating a new session.
        }
      }

      const freshSession = await createChatSession(sessionToken);
      if (!freshSession?.session_id) {
        throw new Error("Failed to initialize chat session");
      }
      writeProjectSession(selectedProjectId, freshSession.session_id);
      setSessionId(freshSession.session_id);
      const freshMessages = Array.isArray(freshSession.messages) ? freshSession.messages : [];
      setMessages(freshMessages);
      setSnapshot(null);
      setGitDiff("");
    };

    initializeSession().catch((err) => {
      setError(err instanceof Error ? err.message : "Failed to initialize chat session");
    });
  }, [sessionToken, selectedProjectId]);

  useEffect(() => {
    if (!sessionToken || !selectedProjectId) {
      setProjectRepositories([]);
      return;
    }
    listProjectRepositories(sessionToken, selectedProjectId)
      .then((rows) => setProjectRepositories(rows))
      .catch(() => setProjectRepositories([]));
  }, [sessionToken, selectedProjectId]);

  const handleSend = async () => {
    if (!prompt.trim() || !sessionToken || !sessionId) {
      return;
    }
    setSending(true);
    setError("");
    const message = prompt.trim();
    setPrompt("");
    try {
      await writeWorkspaceEvent(sessionToken, {
        name: "frontend_send_message",
        input: { message, project_id: selectedProjectId },
      });

      const payload = await sendChatMessage(sessionToken, {
        session_id: sessionId,
        message,
        repos_in_scope: projectRepositories
          .map((repo) => repo.full_name)
          .filter((item) => typeof item === "string" && item),
      });

      const nextMessages = Array.isArray(payload?.session?.messages) ? payload.session.messages : [];
      setMessages(nextMessages);
      setGitDiff(createDiffFromRun(payload?.run));
      writeProjectSession(selectedProjectId, sessionId);

      const run = payload?.run;
      const traceId = typeof run?.trace_id === "string" ? run.trace_id : "";
      if (traceId && runHasAgenticTrace(run)) {
        const traceSnapshot = await pullTraceSnapshot(sessionToken, traceId);
        setSnapshot(traceSnapshot);
      } else {
        setSnapshot(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setSending(false);
    }
  };

  return (
    <main className="layout-grid" aria-label="Reapo project workspace">
      <Sidebar
        repositories={projectRepositories}
      />
      <CenterPanel
        messages={messages}
        prompt={prompt}
        onPromptChange={setPrompt}
        onSend={handleSend}
        sending={sending}
        error={error}
        gitDiff={gitDiff}
      />
      <RightPanel snapshot={snapshot} />
    </main>
  );
}
