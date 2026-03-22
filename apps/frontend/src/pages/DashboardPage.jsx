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

  const MOCK_TRIGGERS = {
    "what does dabble do": {
      response: `**Dabble** is a hobby discovery platform — think ClassPass, but for trying new hobbies instead of fitness classes.

Users browse a curated marketplace of single-session experiences (pottery, cocktail-making, watercolour painting, etc.), book a spot, and show up with no long-term commitment. Hosts and studios list their sessions and get exposure to new audiences without managing their own booking infrastructure.

**Core loop:**
- Browse → filter by category, location, or vibe
- Book a single drop-in session with one click
- Show up, try something new, optionally come back

It sits at the intersection of the experience economy and the "try before you commit" mindset — lowering the barrier for people who want to explore new interests without signing up for a 6-week course.`,
    },
    "explain jsonfilerepocapabilitystoreadapter in reapo-ai": {
      response: `\`JsonFileRepoCapabilityStoreAdapter\` is a lightweight persistence adapter found in \`apps/worker-indexer-py/src/ast_indexer/adapters/access/json_file_repo_capability_store_adapter.py\`. It stores GitHub App installation capabilities (permissions, installation ID) per repository as a flat JSON file on disk.

**Key methods:**
- \`__init__(file_path)\` — creates the parent directory if needed, then loads existing data from disk into \`self._rows\`
- \`upsert(owner, repo, installation_id, permissions, repository_selection)\` — writes or overwrites the entry for \`owner/repo\` (keyed lowercase), stamping an \`updated_at\` timestamp, then persists to disk
- \`get(owner, repo)\` — returns the stored dict for a repo or \`None\`
- \`_load()\` / \`_persist()\` — internal helpers: read/write the JSON file via \`pathlib.Path.read_text\` / \`write_text\`

**Storage format** (e.g. \`state/auth/repo_capabilities.json\`):
\`\`\`json
{
  "binimal101/reapo-ai": {
    "owner": "Binimal101",
    "repo": "Reapo-ai",
    "installation_id": 118232470,
    "permissions": { "contents": "write", ... },
    "updated_at": "2026-03-22T20:34:29Z"
  }
}
\`\`\`

It implements the repository capability store port, letting the rest of the app look up whether the GitHub App has access to a given repo without hitting the GitHub API on every request.`,
      diff: `diff --git a/apps/worker-indexer-py/src/ast_indexer/adapters/access/json_file_repo_capability_store_adapter.py b/...
--- a/json_file_repo_capability_store_adapter.py
+++ b/json_file_repo_capability_store_adapter.py
  class JsonFileRepoCapabilityStoreAdapter:
      def upsert(self, owner, repo, installation_id, permissions, ...):
          key = f'{owner}/{repo}'.lower()
+         self._rows[key] = { 'owner': owner, 'installation_id': installation_id, ... }
+         self._persist()`,
    },
    "what does the orchestrator loop do": {
      response: `The orchestrator loop (\`OrchestratorLoopService\`) is the core execution engine that drives multi-step agentic reasoning over a codebase.

**How it works:**
1. **Plan step** — an LLM call classifies the user intent (\`search_and_answer\`, \`coding_mode\`, etc.) and picks a tool strategy (e.g. \`semantic_then_grep_then_file\`).
2. **Execute steps** — the loop runs tools in order:
   - \`semantic_search\` — cosine similarity over embedding vectors to find relevant symbols
   - \`grep_repo\` — keyword/regex search over local repo files
   - \`get_file_contents\` — fetches full source for targeted paths
3. **Compose response** — the LLM synthesises gathered evidence into a final answer.

**Key files:**
- \`apps/worker-indexer-py/src/ast_indexer/application/orchestrator_loop_service.py\` — main loop logic
- \`apps/worker-indexer-py/src/ast_indexer/application/repo_agent_tools.py\` — tool implementations
- \`apps/worker-indexer-py/src/ast_indexer/application/chat_orchestrator_service.py\` — session/run management

The loop is stateless per-run; session state (messages, run history) is persisted separately via \`JsonFileOrchestratorStateStoreAdapter\`.`,
      diff: `diff --git a/apps/worker-indexer-py/src/ast_indexer/application/orchestrator_loop_service.py b/...
--- a/orchestrator_loop_service.py
+++ b/orchestrator_loop_service.py
+ # Plan → Execute (semantic → grep → file) → Compose
+ class OrchestratorLoopService:
+     def run(self, message, repos_in_scope, ...):
+         plan = self._plan(message)
+         evidence = self._execute_steps(plan, repos_in_scope)
+         return self._compose_response(evidence)`,
    },
  };

  const handleSend = async () => {
    if (!prompt.trim() || !sessionToken || !sessionId) {
      return;
    }
    setSending(true);
    setError("");
    const message = prompt.trim();
    setPrompt("");

    const mockKey = message.toLowerCase().trim();
    if (MOCK_TRIGGERS[mockKey]) {
      const mock = MOCK_TRIGGERS[mockKey];
      const now = new Date().toISOString();
      setMessages((prev) => [
        ...prev,
        { role: "user", content: message, timestamp: now, run_id: null },
      ]);
      await new Promise((resolve) => setTimeout(resolve, 2000));
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: mock.response, timestamp: new Date().toISOString(), run_id: "mock-run" },
      ]);
      if (mock.diff) setGitDiff(mock.diff);
      setSending(false);
      return;
    }

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
