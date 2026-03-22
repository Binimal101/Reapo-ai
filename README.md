# Reapo-ai
Multi-repo coding agent orchestration for research, read/writing, and development.

## Phase 1 Worker Indexer Runbook

### Prerequisites
- Python virtual environment available at .venv
- Docker Desktop running
- Repository root .env configured (see .env.example)

### Run test suite
From apps/worker-indexer-py:

```powershell
c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai/.venv/Scripts/python.exe -m pytest
```

### Run end-to-end smoke
From repository root:

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/phase1_smoke.ps1
```

Expected result:
- Script prints [phase1-smoke] success
- Response payload includes processed=true
- Response payload includes correlation_id and user_id

### Start compose core services manually
From repository root:

```powershell
docker compose -f infra/docker/docker-compose.phase1.yml up -d postgres redis langfuse
docker compose -f infra/docker/docker-compose.phase1.yml ps
```

### Production-like full stack with repo-level env file

This stack starts postgres, redis, langfuse, backend, and frontend.

From repository root you can use **`run.ps1`** (Windows) or **`run.sh`** (Git Bash / WSL / macOS / Linux): same as `docker compose ... up -d --build` for the local stack. Use `./run.sh remote` or `.\run.ps1 -Stack remote` for the remote compose file.

Full stack (defaults are in the compose file; optional `.env` at repo root overrides):

**Local**

```powershell
docker compose -f infra/docker/docker-compose.local.yml --project-name reapo-local up -d --build
```

**Remote / droplet**

```powershell
docker compose -f infra/docker/docker-compose.remote.yml --project-name reapo-remote up -d --build
```

Stop (example — local):

```powershell
docker compose -f infra/docker/docker-compose.local.yml --project-name reapo-local down
```

See `infra/docker/README.md` for details.

### Validate required Phase 1 secrets and auth settings
From repository root:

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/validate_phase1_env.ps1 -Strict
```

This script validates required environment variables and fails fast on placeholder or weak values.

## GitHub App Auth Endpoints

The webhook server exposes GitHub App OAuth and installation capability helpers:

- `GET /auth/github/status`
- `GET /auth/github/start?state=<value>&redirect_uri=<uri>`
- `GET /auth/github/callback?code=<oauth_code>&state=<value>&redirect_uri=<uri>`
- `POST /auth/github/installation-token`
- `GET /auth/github/access?owner=<owner>&repo=<repo>`
- `POST /auth/github/webhook/register`

`POST /auth/github/installation-token` accepts:

```json
{
	"installation_id": 12345,
	"owner": "octo-org",
	"repo": "checkout-service",
	"operation": "read"
}
```

`operation=write` enforces installation `contents:write` capability and returns 403 if write access is not granted.

## Frontend Auth + Chat API

The API server is implemented in:

- `apps/worker-indexer-py/src/ast_indexer/server.py`

### OAuth signup/signin endpoints (frontend-friendly)

1. Start OAuth signup

- `POST /auth/oauth/signup/start`
- Body:

```json
{
	"provider": "github",
	"redirect_uri": "https://your-frontend.example.com/auth/callback",
	"state": "optional-csrf-state"
}
```

2. Start OAuth signin

- `POST /auth/oauth/signin/start`
- Body is same as signup/start.

3. Complete OAuth callback

- `POST /auth/oauth/callback`
- Body:

```json
{
	"provider": "github",
	"flow": "signin",
	"code": "oauth_code_from_provider",
	"state": "state_from_start",
	"redirect_uri": "https://your-frontend.example.com/auth/callback"
}
```

Response includes `session_token` (Bearer token) and `user` payload.

4. Validate session token

- `POST /auth/session/validate`
- Header: `Authorization: Bearer <session_token>`

### Middleware behavior

- Chat endpoints now require Bearer auth.
- Middleware reads `Authorization: Bearer <session_token>`.
- Missing/invalid token returns `401` with `WWW-Authenticate: Bearer`.
- Session and run reads are scoped to authenticated user identity.

### Chat endpoints for frontend

1. Create chat session

- `POST /chat/sessions`
- Required header: Bearer token
- Body (optional):

```json
{
	"user_id": "optional-must-match-auth-user"
}
```

2. Send message

- `POST /chat/messages`
- Required header: Bearer token
- Body params:

```json
{
	"session_id": "required",
	"message": "required",
	"repos_in_scope": ["optional-repo-name"],
	"top_k": 8,
	"candidate_pool_multiplier": 6,
	"relevancy_threshold": 0.35,
	"relevancy_workers": 6,
	"reducer_token_budget": 2500,
	"reducer_max_contexts": 5
}
```

3. Session/run retrieval

- `GET /chat/sessions/<session_id>`
- `GET /chat/runs/<run_id>`
- Both require Bearer token.

### Writer endpoint for frontend (Phase 8)

Create/reuse branch, apply file changes, and open (or reuse) a pull request:

- `POST /writer/pr`
- Required header: `Authorization: Bearer <session_token>`
- Body:

```json
{
	"owner": "acme-inc",
	"repo": "checkout-service",
	"base_branch": "main",
	"title": "fix: checkout validation edge case",
	"body": "Generated by writer flow.",
	"files": [
		{
			"path": "src/checkout/validator.py",
			"content": "<full file content>"
		}
	],
	"branch_name": "reapo-ai/checkout-fix-001",
	"commit_message": "fix: checkout validation edge case",
	"draft": false,
	"dry_run": false
}
```

Notes:

- `files` is required and must be non-empty.
- `dry_run=true` returns the write plan without mutating GitHub.
- If an open PR already exists for the same head/base pair, it is reused.
- Requires repo write capability (`contents:write` or `admin`).

## Phase 1-3 Integration Matrix

Run the following validation sequence after infrastructure or auth changes:

1. Environment validation

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/validate_phase1_env.ps1 -Strict
```

2. Worker-indexer unit tests

```powershell
Set-Location apps/worker-indexer-py
c:/Users/matth/OneDrive/Desktop/programs/personal_brand/Reapo-ai/.venv/Scripts/python.exe -m pytest -q
```

3. Webhook + queue + worker smoke

```powershell
Set-Location ../..
powershell -ExecutionPolicy Bypass -File infra/scripts/phase1_smoke.ps1
```

Expected outcomes:

- OAuth token storage uses encrypted-at-rest adapter when `AST_INDEXER_OAUTH_TOKEN_STORE_PATH` and `AST_INDEXER_OAUTH_ENCRYPTION_KEY` are set
- Duplicate GitHub delivery IDs are deduplicated by replay guard and return status `ignored`
- Installation token endpoint returns `needs_installation` for repos where the GitHub App is not installed
- Capability records are persisted and queryable via `GET /auth/github/access`

## Phase 7 Chat + Orchestrator API

The worker-indexer server now exposes frontend-facing chat endpoints:

- `POST /chat/sessions`
- `POST /chat/messages`
- `GET /chat/sessions/<session_id>`
- `GET /chat/runs/<run_id>`

Orchestrator tool behavior:

- Tool loop is cyclical and capped at 5 iterations per run.
- Includes `grep_repo` tool for indexed signature lookups before semantic search.

`grep_repo` tool definition:

- `query` (string): text filter applied to indexed path/symbol/signature fields
- `repos_in_scope` (string[]): optional repository filter
- `page` (int, 1-based): selects which result page to fetch
- `page_size` (int): max results per page
- `signature_max_chars` (int): hard max characters per returned signature

Pagination semantics:

- `page=1` returns the first page.
- `has_more=true` means call again with `page+1`.
- Signatures are always truncated to at most `signature_max_chars`.

Create session request body:

```json
{
	"user_id": "user-123"
}
```

Send message request body:

```json
{
	"session_id": "<session_id>",
	"user_id": "user-123",
	"message": "Where is checkout validation handled?",
	"repos_in_scope": ["checkout-service"],
	"top_k": 8,
	"candidate_pool_multiplier": 6,
	"relevancy_threshold": 0.35,
	"relevancy_workers": 6,
	"reducer_token_budget": 2500,
	"reducer_max_contexts": 5
}
```

## Manual Langfuse Validation (Phase 7)

1. Start dependencies:

```powershell
docker compose -f infra/docker/docker-compose.phase1.yml up -d postgres redis langfuse
```

2. Export runtime configuration in the shell where the server runs:

```powershell
$env:AST_INDEXER_OBSERVABILITY_BACKEND = "langfuse"
$env:LANGFUSE_HOST = "http://localhost:3000"
$env:LANGFUSE_PUBLIC_KEY = "<public_key>"
$env:LANGFUSE_SECRET_KEY = "<secret_key>"
```

3. Start server and issue one chat flow (`create session` then `send message`).

4. In Langfuse UI, verify traces include:
- `orchestrator_loop` span (chat orchestration lifecycle)
- nested research spans from pipeline execution
- `session_id` and `user_id` metadata in orchestrator span
- terminal status (`completed` or `failed`) on the orchestrator run

5. Failure-path check: send an invalid chat payload and confirm an error response is returned while trace ingestion still remains healthy for valid calls.
