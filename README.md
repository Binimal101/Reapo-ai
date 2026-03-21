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
