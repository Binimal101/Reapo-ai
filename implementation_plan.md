# Implementation Plan: Multi-Repo Coding Agent (From Scratch)

## 1) Purpose
Build the full system described in plan.md from zero to production readiness, including:
- GitHub OAuth and repo access control
- Hybrid RAG indexing and live repo reading
- Parallel relevancy and recursive reducer pipelines
- Stateful orchestrator with delegated sub-agents
- Writer flow that opens pull requests
- End-to-end observability with self-hosted Langfuse and MCP diagnostics

This plan is split into:
- Agent execution steps (what to code and in what order)
- Human intervention checkpoints (tokens, accounts, infrastructure approvals)

The plan explicitly includes a frontend-facing middleware API for:
- account creation and identity linking
- GitHub sync and installation onboarding
- user-defined "systems" composed of multiple repositories

---

## 2) Delivery Strategy
Use an iterative vertical-slice approach:
1. Build a minimal end-to-end path first (single repo, read-only, no PR write).
2. Add observability and quality gates early.
3. Scale to multi-repo + write path only after core loop is stable.
4. Harden for production (security, rate limits, retries, runbooks).

### 2.1) Reality Check Notes (Do Not Regress)
These notes capture implementation-time learnings and must remain true unless intentionally changed:
1. Phase 4 is a bounded LangGraph read-only retrieval slice. It is not expected to produce very large context sets.
2. Candidate breadth in Phase 4 is constrained by:
   - explicit repo scope provided at runtime
   - semantic query count produced by the prodder node
   - top-k cap (CLI default is 8 unless overridden)
3. "Large" cross-repo breadth is a Phase 5 expectation and depends on iterative expansion/reducer behavior, not the Phase 4 single-pass graph.
4. In Langfuse mode, non-hex trace IDs (for example, `research-*`) are normalized to provider-compatible 32-hex trace IDs.
5. When trace normalization occurs, the original caller trace ID must be retained in metadata (for example, `original_trace_id`) for debugging.
6. `parse_python_file` span floods indicate indexing activity, not necessarily research-graph node expansion.

---

## 3) System Decomposition
Major services/modules to implement:
1. frontend/ (optional thin UI for auth, run trigger, run status)
2. api-gateway/ (auth/session, run orchestration API)
3. github-access/ (OAuth, token refresh, webhook handling)
4. middleware-api/ (frontend BFF for accounts, systems, memberships, repo linking)
5. indexer/ (incremental symbol extraction + embeddings)
6. vector-store/ adapters (pgvector or managed provider)
7. research-pipeline/ (semantic prodding + live repo reader)
8. relevancy-engine/ (parallel scoring workers)
9. reducer-engine/ (tiered context compression)
10. orchestrator/ (state machine + tool routing)
11. writer-agent/ (branch, commit, PR flow)
12. memory-agent/ (rolling summary policy)
13. observability/ (Langfuse integration + alerting)
14. infra/ (docker compose, env templates, deployment)

---

## 4) Build Plan for Code Generation (Agent-Execution Steps)

### Phase 0 - Bootstrap Repository and Standards
Goal: Establish project skeleton and engineering guardrails.

Steps:
1. Create monorepo structure:
   - apps/api
   - apps/worker-indexer
   - apps/worker-orchestrator
   - apps/worker-writer
   - apps/web (optional)
   - packages/common
   - packages/prompts
   - packages/clients
   - infra/docker
2. Add language/runtime choice (recommended: TypeScript + Node 20).
3. Add lint, format, typecheck, unit test, integration test commands.
4. Add env templates:
   - .env.example per app
   - secret naming conventions and required variables list
5. Add CI workflow:
   - install, lint, typecheck, test
   - block merge on failures

Deliverables:
- Monorepo scaffolding and green CI on empty baseline

---

### Phase 1 - Local Infrastructure and Observability First
Goal: Bring up local dependencies and trace collection before core logic.

Steps:
1. Add docker compose for:
   - Postgres
   - Redis (queue/cache)
   - Langfuse
   - (optional) local vector DB if separate
2. Implement Langfuse client wrapper in packages/clients:
   - trace start/end
   - span start/end
   - score recording helper
3. Add correlation ID middleware in API and workers.
4. Implement health endpoints and readiness checks.

Deliverables:
- docker compose up works locally
- test endpoint emits a trace and span visible in Langfuse

---

### Phase 2 - GitHub OAuth + Access Control Layer
Goal: Securely access repositories with scoped permissions.

Steps:
1. Implement GitHub App OAuth flow:
   - start auth
   - callback exchange
   - installation token retrieval
2. Encrypt and store session token metadata at rest.
3. Implement token refresh logic:
   - refresh before expiry
   - retry on 401
4. Build access model:
   - read/write/admin capability mapping per repo
   - access check helper for orchestrator and writer
5. Implement webhook registration endpoint and signature verification.

Deliverables:
- User can connect repos
- System can verify read/write access per repo
- push webhook events are received and authenticated

---

### Phase 2.5 - Frontend Middleware API (Accounts, Systems, Multi-Tenant Auth)
Goal: Provide a frontend-ready API where users create accounts, sync GitHub, and define multi-repo systems with safe tenant isolation.

Steps:
1. Define multi-tenant data model and contracts:
   - User
   - TenantAccount
   - System (logical workspace)
   - SystemRepo (many repos per system)
   - Membership/role
   - GithubIdentity/GithubInstallationLink
2. Build account/session endpoints for frontend:
   - sign-up/sign-in/session status
   - connect/disconnect GitHub identity
   - account profile and org membership views
3. Build systems API:
   - create/update/archive system
   - attach/detach repositories to a system
   - list systems and repos by tenant/membership
4. Implement multi-tenant authorization middleware:
   - enforce tenant boundary on every request
   - enforce role checks (owner/admin/member/viewer)
   - enforce per-repo capability checks (read/write/admin)
5. Implement token broker endpoints for orchestrator/writer:
   - resolve installation by owner/repo or installation_id
   - mint short-lived installation tokens on demand
   - return clear install-required responses instead of generic failures
6. Add installation onboarding flow endpoints:
   - generate install URL for missing installation
   - callback/confirmation endpoint after install
   - retry token mint after installation is completed
7. Add audit and abuse controls:
   - audit log for permission and token events
   - request rate limits per account/system
   - suspicious activity flags (token churn, repeated denied writes)
8. Add integration tests for tenant isolation and cross-repo systems:
   - user A cannot access user B systems
   - system with 2+ repos can read/query both
   - write request blocked when repo permission is read-only

Deliverables:
- Frontend can create accounts and sync GitHub identity
- Frontend can create systems spanning multiple repositories
- Middleware returns tenant-safe, permission-aware token responses for r/w operations
- Missing installation state is a guided onboarding response, not an opaque error

---

### Phase 3 - Indexing Pipeline (Incremental)
Goal: Build and maintain symbol index from push events.

Steps:
1. Implement webhook job ingestion queue.
2. Build changed-file resolver from push payload.
3. Implement file fetch + AST symbol extraction:
   - function/class/method signature extraction
   - docstring/comments extraction
4. Generate embeddings for signature + docstring.
5. Upsert vector records with metadata:
   - repo, path, kind, tree_sha, blob_sha, access_level
6. Implement delete path for removed files/symbols.
7. Add index build spans in Langfuse with metrics.

Deliverables:
- Incremental index updates after pushes
- index build traces with symbol counts and latency

---

### Phase 4 - Research Pipeline (LangGraph Read-Only MVP)
Goal: Convert a raw prompt into a structured research objective, generate semantic queries, and perform live codebase retrieval via a bounded LangGraph state machine.

Scope guardrail for this phase:
- This phase is intentionally single-pass and bounded by top-k.
- Do not treat low candidate counts here as Phase 5 regressions.

**Iterative Development Steps:**
1. **LangGraph State Definition & Initialization:**
   - Define the `ResearchState` graph state typed schema (e.g., `raw_prompt`, `research_objective`, `search_queries`, `candidate_snippets`, `enriched_context`).
   - Scaffold the LangGraph runner class and its core edges (`reasoning_node` -> `prodder_node` -> `retrieval_node`).
2. **Implement Reasoning Agent Node:**
   - Write prompt templates to instruct the LLM to parse `raw_prompt` into a structured `ResearchObjective` (intent, explicit entities, repos in scope).
   - Hook up Langchain/OpenAI wrapper and emit `reasoning_agent` trace span.
3. **Implement Semantic Prodder Node:**
   - Translate the `ResearchObjective` into an array of context-rich query strings designed for vector retrieval.
4. **Vector Search & Deduplication Logic:**
   - Integrate vector store API to fetch top-K indexed symbols matching the generated query strings.
   - Combine and deduplicate returned signature records.
5. **Build Live Repo Reader Node:**
   - Receive candidate `sig_ids` and fetch raw Python blobs using the GitHub API adapter.
   - Implement ETag cache wraping to avoid re-fetching unchanged blobs.
   - Parse AST of downloaded blobs to extract the symbol body and its immediate `callees` (up to depth 2 or 3).
6. **Graph Compilation & Observability Wiring:**
   - Connect nodes into the compiled LangGraph execution graph.
   - Ensure each node emits distinct spans and metrics (e.g., cache hits, blob fetches, token usage) into Langfuse via the LangGraph native callbacks or custom middleware.

**Testing Strategy:**
- **Unit Tests:** Validate specific Node boundaries (e.g., ensure Reasoning Agent returns a valid Pydantic/JSON schema for `ResearchObjective`).
- **Mocked Integration:** Run the compiled LangGraph with a mocked Vector DB and GitHub API to assert state transitions happen correctly (prompt -> objective -> queries -> fetched blobs).
- **Evaluation:** Inject 3 distinct prompt styles ("Where is XYZ defined?", "How does auth work?", "Fix the bug in the order processor") and assert the generated search queries are logically sound.

**Usage Example:**
```python
from research_pipeline.graph import ResearchGraph

# Initialize the LangGraph-based research pipeline
graph = ResearchGraph(vector_store=pgvector_adapter, github_api=gh_client)

# Execute the graph synchronously for a user request
final_state = graph.invoke({
    "raw_prompt": "How does the webhook signature verification actually work in the python worker?",
    "repos_in_scope": ["worker-indexer-py"]
})

# Access the resulting enriched call-graphs from the terminal state
print(f"Objective parsed: {final_state['research_objective'].intent}")
for candidate in final_state['enriched_context']:
    print(f"Found {candidate.symbol} in {candidate.path}, body length: {len(candidate.body)}")
```

---

### Phase 5 - Parallel Relevancy System
Goal: Filter and rank context with confidence-based scoring and support larger cross-repo candidate frontiers.

Expected behavior shift from Phase 4:
- This phase is where breadth should increase meaningfully via broader candidate sets and parallel scoring.
- If breadth remains small, validate repo scope, expansion policy, and threshold settings before blaming reducers.

Steps:
1. Implement worker pool and load balancer.
2. Define RelevancyResult schema and confidence score rules.
3. Execute candidate scoring in parallel with timeout/cancel handling.
4. Implement collector thresholding and rank output.
5. Emit per-agent spans and relevancy_confidence scores.
6. Add low-confidence alert rule wiring.

Deliverables:
- Ranked relevant set from parallel workers
- confidence distribution chart in Langfuse

---

### Phase 6 - Recursive Reducer System
Goal: Fit selected context into token budget while preserving key symbols.

Steps:
1. Implement reducer batch planner and tier scheduler.
2. Define reducer input/output contracts.
3. Build token budget calculator and stop condition.
4. Preserve mandatory entities:
   - symbol IDs
   - repo names
   - file paths
   - open questions
5. Add overrun safeguards and tier max limits.
6. Emit reducer tier spans and compression metrics.

Deliverables:
- Final context block under target token budget
- reducer overrun alert configured

---

### Phase 7 - Orchestrator Core Loop
Goal: Stateful, tool-using orchestrator that can plan and execute steps.

Steps:
1. Implement orchestrator state machine:
   - plan
   - execute step
   - tool check
   - memory check
   - done
2. Add tool interfaces:
   - search(query, repos?)
   - write(...)
   - memory_summary()
   - langfuse diagnostics tools
3. Implement context utilization tracking and summary trigger at 60%.
4. Persist run state and step outputs for resumability.
5. Add failure recovery and retry policies.

Deliverables:
- Orchestrator can complete a read-only run end to end
- trace contains orchestrator_loop and orchestrator_step spans

---

### Phase 8 - Writer Sub-Agent + Pull Request Flow
Goal: Turn orchestrator intent into actual branch commits and PRs.

Steps:
1. Implement diff application strategy (safe patch creation).
2. Create branch naming and collision handling.
3. Commit file updates through GitHub API.
4. Open PR with structured title/body template.
5. Handle idempotency:
   - existing branch
   - existing PR reuse
6. Enforce write access checks before every write.
7. Emit writer spans with repo/path/pr_url metadata.

Deliverables:
- Successful PR creation in write-enabled repo
- blocked-write behavior in read-only repo

---

### Phase 9 - Memory Agent and Context-Rot Prevention
Goal: Keep long runs coherent.

Steps:
1. Implement rolling summary schema and serializer.
2. Preserve non-droppable fields:
   - symbols
   - access levels
   - opened PR URLs
   - open questions
3. Replace old turns with summary + recent tail.
4. Add summary quality checks.
5. Trace memory operations with token reduction metrics.

Deliverables:
- Stable multi-step runs beyond base context window

---

### Phase 10 - Langfuse MCP Self-Diagnosis
Goal: Allow orchestrator to introspect previous runs.

Steps:
1. Connect orchestrator to Langfuse MCP server.
2. Implement helper tools:
   - get_trace
   - get_spans
   - get_scores
3. Add fallback behavior when MCP is unavailable.
4. Add prompts/policies for when to self-diagnose.

Deliverables:
- Orchestrator can detect prior failure patterns and adjust strategy

---

### Phase 11 - Security, Reliability, and Production Hardening
Goal: Make system safe and operable.

Steps:
1. Add secret management integration (vault/KMS/env injection).
2. Enforce webhook signature verification and replay protection.
3. Add rate-limit handling with exponential backoff + jitter.
4. Add queue DLQ and retry policies per worker type.
5. Add audit logging for write actions and permission checks.
6. Add SLOs:
   - run success rate
   - p95 latency
   - token cost per run
7. Build runbook and incident response docs.

Deliverables:
- Production readiness checklist complete

---

### Phase 12 - Test Matrix and Launch
Goal: Validate functionality and launch in controlled rollout.

Steps:
1. Unit tests per module.
2. Integration tests:
   - OAuth flow
   - webhook to index update
   - prompt to ranked context
   - prompt to PR creation
3. End-to-end scenario tests across 2+ repos.
4. Adversarial tests:
   - token expiration mid-run
   - API rate limit
   - read-only repo write attempt
   - missing blobs/call-graph gaps
5. Canary rollout with feature flags.
6. Observe first production runs and tune thresholds.

Deliverables:
- Launch report with pass/fail by scenario
- tuned defaults for K, confidence threshold, and reducer tiers

---

## 5) Human-Intervention Checkpoints (Required)

These cannot be fully automated and require owner action.

### A) Accounts, Access, and Legal
1. Create and configure GitHub App (name, callback URL, webhook URL).
2. Approve required GitHub App scopes for intended repositories.
3. Install GitHub App on target org/repositories.
4. Confirm legal/compliance approval for repository indexing and AI processing.

### B) Secrets and Credentials
1. Provide API tokens/keys:
   - LLM provider API key(s)
   - embedding provider key (if separate)
   - Langfuse secret/public keys
   - database credentials
   - Redis credentials
2. Provide GitHub App credentials:
   - App ID
   - private key
   - webhook secret
   - client ID/client secret (if needed by flow)
3. Set up secret storage and rotation policy.

### C) Infrastructure Decisions
1. Choose deployment environment:
   - local/self-hosted only
   - cloud VM/Kubernetes
2. Choose vector store backend.
3. Choose Postgres hosting strategy.
4. Set retention policy for traces and index records.

### D) Governance and Guardrails
1. Approve write policy:
   - allow auto-PR creation? yes/no
   - allowed branches and repo allowlist
2. Define human approval gate before PR creation (if required).
3. Define observability access policy (who can view traces and diffs).

### E) Validation and Sign-Off
1. Validate generated PR quality on pilot repos.
2. Sign off on alert thresholds and on-call ownership.
3. Approve production rollout and rollback criteria.

### F) Human Workflow Outside Code Scope
1. Define customer onboarding flow and ownership:
   - who assists first-time GitHub App installation for users/org admins
   - expected response times and escalation path for install failures
2. Define support runbooks for access incidents:
   - repo not visible in system
   - installation exists but token mint fails
   - write permission denied after role change
3. Define trust and consent language:
   - explain what repository metadata/code is read
   - explain where data is stored and retention period
   - publish revocation and data deletion process
4. Define account lifecycle operations:
   - member invite/approval policy for shared systems
   - offboarding and immediate access revocation checklist
   - duplicate account merge and ownership transfer procedure
5. Define governance for manual overrides:
   - when support can temporarily unblock access
   - approval chain for emergency write enablement
   - post-incident review requirement for every manual override

---

## 6) Recommended Task Order for Ongoing Code Generation Sessions
Use this sequence for future implementation sessions with the coding agent:
1. Scaffold repo + infra + env contracts.
2. Build OAuth + webhook + token refresh.
3. Build incremental indexer + embedding upsert.
4. Build research pipeline with live blob reader.
5. Add parallel relevancy and collector.
6. Add recursive reducer.
7. Add orchestrator state machine + tool APIs.
8. Add writer sub-agent + PR creation.
9. Add memory summarizer.
10. Add Langfuse MCP self-diagnosis.
11. Harden reliability/security.
12. Run full E2E and launch.

---

## 7) Definition of Done
The system is considered implemented when all are true:
1. A user can connect GitHub repos via OAuth and run a multi-repo task.
2. The system performs live research, filtering, reduction, and orchestration.
3. Writer sub-agent opens valid PRs in write-enabled repos only.
4. Every handoff emits Langfuse spans with input/output, usage, and latency.
5. Alert rules trigger correctly for low confidence and reducer overrun.
6. End-to-end tests pass for happy path and failure paths.
7. Security and operational runbooks are in place.
