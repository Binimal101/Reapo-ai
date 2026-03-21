# Phase 7 Execution Plan: Frontend Chat API + Orchestrator Core Loop

## Scope
Deliver Phase 7 with production-oriented API and orchestrator loop, integrated with existing worker-indexer runtime and observability.

## Goals
1. Frontend can create chat sessions and send messages.
2. Messages execute through an orchestrator loop with explicit step state.
3. Orchestrator uses search tool (research pipeline) and emits structured spans.
4. Run state is persisted for resumability and inspection.
5. Endpoints are test-covered with pytest.
6. Manual Langfuse runbook exists for verification.

## Architecture
1. API surface is hosted in existing webhook server process (`ast_indexer.server`).
2. New application service: `OrchestratorLoopService`.
3. New application service: `ChatOrchestratorService`.
4. New persistence adapter: `JsonFileOrchestratorStateStoreAdapter`.
5. Tool abstraction:
   - `search(query, repos_in_scope)` -> `ResearchPipelineResult`
   - `memory_summary(history)` -> string
6. Observability:
   - `orchestrator_loop`
   - `orchestrator_step` (one per state transition)
   - Existing research spans nested in same trace.

## API Contract (v1)
1. `POST /chat/sessions`
   - Body: `{ "user_id": "..." }`
   - Returns: `{ "status": "ok", "session_id": "...", "created_at": "..." }`
2. `GET /chat/sessions/{session_id}`
   - Returns session metadata and message history.
3. `POST /chat/messages`
   - Body: `{ "session_id": "...", "user_id": "...", "message": "...", "repos_in_scope": ["..."], "top_k": 8 }`
   - Returns immediate run result with assistant response and run metadata.
4. `GET /chat/runs/{run_id}`
   - Returns run status, steps, outputs, errors.

## Orchestrator State Machine
1. `plan`
   - Parse user message into objective intent and tool plan.
2. `execute_step(search)`
   - Invoke research pipeline.
3. `execute_step(compose_response)`
   - Build response from reduced context and objective.
4. `memory_check`
   - Optional summarize when message history exceeds threshold.
5. `done`

## Data Model
1. `ChatSession`
   - `session_id`, `user_id`, `created_at`, `updated_at`, `messages`.
2. `ChatMessage`
   - `role` (`user|assistant|system`), `content`, `timestamp`, `run_id`.
3. `OrchestratorRun`
   - `run_id`, `session_id`, `trace_id`, `status`, `started_at`, `finished_at`, `steps`, `final_response`, `error`.
4. `OrchestratorStep`
   - `name`, `status`, `started_at`, `finished_at`, `input`, `output`, `error`.

## Reliability and Production Controls
1. Deterministic planning fallback if any model call fails.
2. Strict request validation and clear 4xx vs 5xx boundaries.
3. Idempotent state writes for run/session updates.
4. Lock-protected file writes in JSON state adapter.
5. Correlation ID attached to all responses and spans.
6. Bounded top-k and token budget defaults.

## Testing Plan (Pytest)
1. Unit tests:
   - `OrchestratorLoopService` happy path.
   - step failure and graceful error state.
   - memory threshold behavior.
2. Adapter tests:
   - state persistence create/read/update for sessions and runs.
3. Server runtime tests:
   - `POST /chat/sessions` validation and success.
   - `POST /chat/messages` executes run and returns assistant response.
   - `GET /chat/runs/{run_id}` and `GET /chat/sessions/{session_id}`.
4. Observability assertions:
   - verify `orchestrator_loop` and `orchestrator_step` spans are emitted.

## Manual Langfuse Validation
1. Start services with `AST_INDEXER_OBSERVABILITY_BACKEND=langfuse` and valid Langfuse keys.
2. Create a chat session via HTTP.
3. Send a chat message.
4. Confirm trace contains:
   - `orchestrator_loop`
   - `orchestrator_step` spans
   - research spans (`reasoning_agent`, `semantic_prodder`, `relevancy_engine`, `reducer_engine`)
5. Confirm trace metadata includes `session_id`, `user_id`, `run_id`.

## Implementation Order
1. Add domain/application models and services.
2. Add JSON state adapter.
3. Wire services in `GithubWebhookServerApp`.
4. Add HTTP handlers for chat routes.
5. Add pytest coverage.
6. Add manual Langfuse runbook section in README.

## Definition of Done for Phase 7 Slice
1. All new pytest tests pass.
2. Chat endpoint round trip works locally.
3. Run/session persistence files are updated correctly.
4. Langfuse manual validation checklist passes when backend is configured.
