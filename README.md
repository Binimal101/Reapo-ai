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
