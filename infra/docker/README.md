# Docker Compose

Two stacks (same services; separate project names and default passwords so volumes do not collide if you use both on one machine).

From **repository root**, `run.ps1` / `run.sh` wrap the same commands (`up -d --build`). Optional argument: `remote` for the droplet stack.

| File | Use | One command |
|------|-----|----------------|
| `docker-compose.local.yml` | Laptop / Docker Desktop | `docker compose -f infra/docker/docker-compose.local.yml --project-name reapo-local up -d --build` |
| `docker-compose.remote.yml` | Droplet / VPS | `docker compose -f infra/docker/docker-compose.remote.yml --project-name reapo-remote up -d --build` |

Run **from repository root** (`Reapo-ai`). All secrets have defaults in the YAML — **no `.env` required**. If `Reapo-ai/.env` exists, Compose loads it and overrides defaults.

- **Fresh DB:** `docker compose ... down -v` then `up -d --build`
- **Langfuse UI:** `http://localhost:3000` (bootstrap, then create API keys; set `AST_INDEXER_OBSERVABILITY_BACKEND=langfuse` and keys in `.env`, recreate `backend`)
- **Phase 1 only** (postgres + redis + langfuse): `docker compose -f infra/docker/docker-compose.phase1.yml up -d postgres redis langfuse`
