# Phase 1-3 Production Readiness Report

Date: 2026-03-20
Scope: implementation_plan.md phases 1, 2, and 3 versus current repository implementation.

## Verdict
- Phase 1: Ready with validated local smoke pass.
- Phase 2: Not production-complete.
- Phase 3: Functional but not production-complete.

## Evidence Snapshot
- Phase 1 smoke executed successfully through infra startup, webhook ingestion, queue, worker processing, and teardown.
- Full pytest suite currently passes.

## Phase 1 Status
Plan reference: implementation_plan.md (Phase 1)

Implemented:
- Docker compose for Postgres, Redis, Langfuse: infra/docker/docker-compose.phase1.yml
- Correlation IDs through webhook path and response payload: apps/worker-indexer-py/src/ast_indexer/server.py
- Health and readiness endpoints: apps/worker-indexer-py/src/ast_indexer/server.py
- Langfuse adapter wiring and strict mode option: apps/worker-indexer-py/src/ast_indexer/main.py
- CI workflow for worker-indexer tests: .github/workflows/worker-indexer-phase1.yml

Gaps for strict production posture:
- Secrets default placeholders remain in compose template and require deployment hardening process.

## Phase 2 Status
Plan reference: implementation_plan.md (Phase 2)

Implemented:
- OAuth start/callback and installation-token endpoints:
  - GET /auth/github/status
  - GET /auth/github/start
  - GET /auth/github/callback
  - POST /auth/github/installation-token
- App JWT signing and installation token creation:
  apps/worker-indexer-py/src/ast_indexer/application/github_app_auth_service.py
- Signature verification exists for webhook handling.

Blocking gaps:
1. Token persistence is in-memory only, not encrypted at rest.
2. Token refresh flow is not implemented for expiring OAuth credentials.
3. Access model is not persisted or enforced per repo capability map beyond returned installation permissions.
4. Webhook registration endpoint is missing.
5. Installation missing state currently bubbles as API error and needs first-class onboarding response contract.

## Phase 3 Status
Plan reference: implementation_plan.md (Phase 3)

Implemented:
- Webhook job ingestion queue with retry/dead-letter behavior.
- Changed/deleted python file resolver from push payload.
- AST symbol extraction and embedding generation.
- Vector upsert with metadata fields including repo/path/kind/tree_sha/blob_sha/access_level.
- Delete path for removed files/symbols.
- Observability spans and indexing metrics.

Blocking gaps:
1. Repo identity from push payload is repo name only by default and is collision-prone across owners.
2. Indexing reader is local filesystem only; production GitHub blob/tree fetch path is not present in this slice.
3. No explicit idempotency or dedupe guard for repeated push delivery IDs.
4. No replay window protection keyed by X-GitHub-Delivery.

## Priority Remediation Order
1. Phase 2 persistence and refresh: encrypted token store + refresh/token rotation behavior.
2. Phase 2 authorization: persisted per-repo capability model and centralized permission checks.
3. Phase 2 onboarding contract: convert missing installation to guided install response shape.
4. Phase 3 identity hardening: owner/repo canonical identifiers in queue, index, and vectors.
5. Phase 3 delivery idempotency and replay protection.

## Go/No-Go
- Production go for full Phase 1-3 scope: NO.
- Production go for current worker-indexer Phase 1 runtime slice only: CONDITIONAL YES (after deployment secret hardening).
