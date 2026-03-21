# Changelog

## Project
Reapo-ai

## Last Updated
2026-03-21

## Purpose of this changelog
This document captures:
- what has been completed so far
- what remains to be built
- how the next implementation steps are sequenced and validated

It is intended to be the operational handoff record between implementation sessions.

---

## 1. Executive Status

### Overall status
- In progress
- Foundational Python worker slice is implemented and tested
- Coverage gate is active and passing above threshold

### Current implementation focus
- Python-only repository indexing flow
- Ports and adapters architecture
- Observability instrumentation around application services
- AST symbol extraction and call graph baseline

### Quality gates status
- Unit tests: passing
- Coverage threshold: active (fail-under 60)
- Latest measured coverage for worker-indexer-py: 91.36%

---

## 2. What has been completed

## 2.1 Planning and documentation milestones

### Completed
- Initial architecture planning artifacts were produced and iterated
- Full implementation roadmap was authored in implementation_plan.md
- Human-intervention checkpoints were documented in implementation_plan.md (OAuth credentials, infra, security approvals, governance)

### Notes
- The implementation roadmap currently targets a broader multi-service platform and includes non-Python modules in future phases, while current build work has intentionally started with a Python-first vertical slice.

---

## 2.2 Repository and environment preparation

### Completed
- Workspace cleanup actions were run to remove noisy generated dependency state during implementation
- Python environment configured for the repository virtual environment
- Test dependencies installed for the Python worker module

### Validation
- Test command run from worker package succeeded
- No coverage-gate violations

---

## 2.3 Python worker-indexer baseline (Phase foundation)

Location: apps/worker-indexer-py

### Completed architecture components

#### Domain
- SymbolRecord model (repo/path/symbol metadata/signature/callees)
- IndexRunMetrics model
- TraceSpan model

#### Ports
- RepositoryReaderPort
- IndexStorePort
- ObservabilityPort
- OAuthTokenStorePort

#### In-memory adapters
- Local filesystem repository reader
- In-memory symbol index store
- In-memory observability adapter
- In-memory OAuth token store

#### Application services
- IndexPythonRepositoryService
  - scans python files from a repo root
  - extracts symbols and call edges
  - upserts symbols
  - emits spans for run and per-file parsing
- OAuthSessionService
  - stores token with expiry
  - resolves only valid tokens
  - emits spans for save and fetch flows

#### Composition
- build_index_service factory in main module for in-memory wiring

### Completed tests
- AST extractor behavior tests
- Index service test (files scanned, symbols indexed, span emission)
- OAuth session service tests (valid and expired token paths)

---

## 2.5 Increment completed in latest session

### Goal
Repository-aware call graph linkage and runnable CLI entrypoint.

### New modules
- `parsing/module_path_resolver.py` — converts repo-relative file paths to dotted module names (strips `src/`, `__init__`, normalises separators)
- `parsing/cross_file_linker.py` — resolves raw callee strings to canonical `path::symbol` IDs across all indexed files using three resolution strategies: exact name match, exact module-qualified match, and suffix match for partial module paths
- `__main__.py` — CLI entrypoint; run with `PYTHONPATH=src python -m ast_indexer --repo <name> --workspace <path> --state <path>`

### Domain changes
- `SymbolRecord` gains `linked_callees: tuple[str, ...]` — resolved canonical callee IDs
- `IndexRunMetrics` gains `linked_edges: int` — total linked edges for the run

### Service changes
- `IndexPythonRepositoryService` restructured to two-phase flow:
  1. Parse all files (per-file spans emitted as before)
  2. Cross-file link all symbols (`link_callees` span emitted with `linked_edges` count)
  3. Upsert enriched symbols
- Constructor gains `linker: CrossFileLinker` and `module_resolver: ModulePathResolver`
- Factory functions in `main.py` updated accordingly

### Persistence changes
- `JsonFileSymbolIndexStoreAdapter` serialises and deserialises `linked_callees`

### New tests
- `test_cross_file_linker.py` — 5 integration tests covering:
  - Cross-file callee resolution (orders → pricing)
  - Intra-file callee resolution (Pricer.compute → apply_discount)
  - Empty linked_callees for leaf functions
  - Persistence and reload of linked_callees
  - `link_callees` span emission and output payload

### Validation
- Test count: 12 passed
- Coverage: 86.97%
- Threshold: pass (required >= 60)
- CLI smoke-test: indexed `worker-indexer-py` itself — 24 files, 88 symbols, 91 linked edges

---

## 2.4 Increment completed in previous session

### Functional improvements

#### Import-aware call graph extraction
- Added import alias collection from:
  - import module
  - from module import name (including aliasing)
- Call target normalization now resolves:
  - alias function calls to fully-qualified symbols where available
  - module alias attribute calls to fully-qualified call names
  - self/cls method calls to ClassName.method

#### Durable persistence adapters
- Added JsonFileSymbolIndexStoreAdapter
  - persists index rows to JSON
  - reloads persisted symbols on startup
  - supports upsert semantics
- Added JsonlFileObservabilityAdapter
  - emits completed spans to JSONL
  - keeps in-memory span list for local assertions/introspection

#### Persistent composition wiring
- Added build_persistent_index_service factory
  - reader: LocalFsRepositoryReaderAdapter
  - index store: JSON-backed adapter
  - observability: JSONL-backed adapter

### New tests added
- Parser test for alias resolution and self-method call mapping
- Adapter test for symbol store persistence/reload
- Adapter test for observability JSONL output

### Validation after increment
- Test count: 7 passed
- Coverage: 91.36%
- Threshold status: pass (required >= 60)

---

## 3. Current code capabilities

### End-to-end capability now available
- Given a local repository directory with Python files:
  - list and read files through port abstraction
  - parse top-level functions/classes/methods
  - collect direct callees with improved normalization
  - store symbols via interchangeable adapters
  - emit per-step observability spans via interchangeable adapters

### Architecture quality achieved
- Clear ports/adapters boundaries
- Application service orchestration separated from infra details
- Swappable adapter strategy proven via in-memory and file-backed implementations

---

## 4. What remains to do

## 4.1 Remaining platform work (high level)

### Not yet implemented
- API gateway and run orchestration endpoints
- Queue-backed asynchronous workers
- GitHub OAuth live integration and token refresh against GitHub APIs
- Webhook ingestion and signature validation pipeline in Python worker path
- Production observability backend integration (Langfuse and MCP diagnostics)
- Vector embedding generation and vector search backend
- Multi-agent orchestration loop (research/relevancy/reducer/writer)
- PR writer flow and repository mutation safeguards

---

## 4.2 Remaining indexing and analysis depth

### Gaps in current AST/call graph implementation
- Cross-file symbol resolution is not yet implemented
- Import-from relative module resolution is not yet normalized to repository module graph
- Nested function scope handling is minimal
- Dynamic dispatch and advanced Python patterns are not modeled
- Only direct call edges are captured (no confidence model)
- No symbol identity namespace beyond repo/path/kind/symbol key

### Gaps in persistence behavior
- No migration/versioning strategy for persisted JSON schemas
- No retention/rotation strategy for JSONL span logs
- No concurrency control for multi-process writer scenarios

---

## 4.3 Operational and production gaps

### Missing hardening
- Secrets management and credential rotation
- Access control policy enforcement for write paths
- Retry/backoff and rate-limit handling
- DLQ strategy for failed jobs
- Alerting and runbooks

### Missing test layers
- Integration tests over multi-file repositories with cross-file call expectations
- End-to-end tests that include persistence adapters and rerun behavior
- Failure mode tests (invalid syntax files, encoding edge cases, partial writes)

---

## 5. Planned next steps

## 5.1 Immediate next increment (next coding session)

### Goal
Queue/job abstraction for index requests and structured correlation IDs across all spans.

### Planned tasks
1. Add `RunRequest` domain model (repo, trace_id, requested_at)
2. Add `JobQueuePort` protocol — enqueue/dequeue run requests
3. Implement `InMemoryJobQueueAdapter` for local/test use
4. Add `run_id` to all span payloads for correlation
5. Add a `WorkerLoop` application service that drains the queue and calls `IndexPythonRepositoryService`
6. Tests for queue draining and correlation ID propagation

### Exit criteria
- Tests pass
- Coverage remains >= 60
- Correlation IDs visible in all span payloads end-to-end

---

## 5.2 Short-term roadmap (2 to 4 increments)

### Increment A
- Repository-aware call graph linking
- CLI and run summaries

### Increment B
- Queue/job abstraction for index requests
- Structured run IDs and correlation IDs across all spans

### Increment C
- Pluggable embedding provider port + simple local/mock embedding adapter
- Metadata-rich symbol records for downstream retrieval

### Increment D
- HTTP API wrapper for index runs and run status retrieval
- Basic authentication boundary for local/dev mode

---

## 5.3 Mid-term roadmap (platform convergence)

### Goals
- Connect Python worker outputs to broader orchestration model in implementation_plan.md
- Keep architecture strictly modular so new services can be integrated without rewrites

### Planned convergence actions
- Standardize contracts for run orchestration payloads
- Add observability event taxonomy shared across services
- Introduce persistence adapters that can be upgraded from JSON files to database-backed storage

---

## 6. Testing and verification record

## 6.1 Current state
- Test framework: pytest
- Coverage tool: pytest-cov
- Fail-under configured in pyproject.toml: 60

## 6.2 Last successful run
- Command: python -m pytest (using project virtual environment executable)
- Result: 7 passed
- Coverage: 91.36%

## 6.3 Quality policy for next sessions
- Every feature increment must include tests
- Every increment must end with full worker test run
- Coverage must never drop below fail-under gate

---

## 7. Risks and mitigation plan

## 7.1 Technical risks
- Risk: call graph false positives due to dynamic Python dispatch
  - Mitigation: explicit confidence flags for linked vs heuristic edges
- Risk: persistence corruption with concurrent writes
  - Mitigation: file locking or move to transactional DB adapter
- Risk: schema drift between stored index records and runtime models
  - Mitigation: add schema version field and migration handler

## 7.2 Product risks
- Risk: broad implementation_plan.md scope diverges from phased Python-first execution
  - Mitigation: maintain this changelog as source of truth for actual completion state
- Risk: observability shape inconsistency across components
  - Mitigation: define shared event/span schema before API and worker expansion

---

## 8. Suggested working agreement for future sessions

1. Start each session by updating this changelog first (status + planned delta)
2. Implement only one clearly-scoped increment at a time
3. End each session with:
   - tests + coverage run
   - changelog update with outcomes and unresolved gaps
4. Avoid coupling next components directly to in-memory adapters; always add via ports

---

## 9. Traceability references

- High-level implementation roadmap: implementation_plan.md
- Python worker package config and coverage gate: apps/worker-indexer-py/pyproject.toml
- Core parser implementation: apps/worker-indexer-py/src/ast_indexer/parsing/python_ast_symbol_extractor.py
- Core index service: apps/worker-indexer-py/src/ast_indexer/application/index_python_repository_service.py
- Composition root: apps/worker-indexer-py/src/ast_indexer/main.py

---

## 10. Next update trigger

Update this changelog after any of the following:
- new adapter added
- parser behavior modified
- orchestration/API layer introduced
- test/coverage baseline changes
- roadmap priorities changed
