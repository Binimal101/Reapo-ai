# Local Docker Compose .env Guide (Langfuse + Reapo)

This guide is for running the full stack locally with Docker Compose and a repo-scoped env file.

## 1) File locations

Expected layout:

- personal_brand/
  - Reapo-ai/
    - .env
    - infra/docker/docker-compose.prodtest.yml

## 2) Minimum required env for Langfuse startup

Set these in Reapo-ai/.env:

```env
# Postgres used by Langfuse
POSTGRES_DB=langfuse
POSTGRES_USER=langfuse
POSTGRES_PASSWORD=replace-with-strong-password

# Langfuse web auth/session
LANGFUSE_NEXTAUTH_URL=http://localhost:3000
LANGFUSE_NEXTAUTH_SECRET=replace-with-long-random-secret
LANGFUSE_SALT=replace-with-long-random-salt

# Langfuse project API keys (set after first bootstrap if blank)
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# Backend webhook secret (required by backend container)
AST_INDEXER_WEBHOOK_SECRET=replace-with-strong-secret

# GitHub App path when backend runs in Docker
GITHUB_APP_PRIVATE_KEY_PATH=/workspace/reapo-ai.2026-03-20.private-key.pem
```

Notes:

- `GITHUB_APP_PRIVATE_KEY_PATH` must be a container path (`/workspace/...`) for Docker backend.
- If you also run backend natively on Windows, keep a separate env profile for host paths.

## 3) Start stack

From repository root `Reapo-ai`:

```powershell
docker compose --env-file ./.env -f infra/docker/docker-compose.prodtest.yml --project-name reapo-prodtest up -d --build
```

## 4) Verify health

```powershell
docker compose --env-file ./.env -f infra/docker/docker-compose.prodtest.yml --project-name reapo-prodtest ps
curl http://localhost:3000/api/public/health
curl http://localhost:8090/auth/github/status
curl http://localhost:8080
```

Expected:

- Langfuse container healthy
- Backend healthy
- Frontend up
- GitHub status returns `configured: true` when GitHub App vars are valid

## 5) Re-bootstrap Langfuse (fresh Postgres)

If Postgres is wiped/new, Langfuse is a fresh instance.

1. Open `http://localhost:3000`
2. Complete first-user/project setup
3. Create Langfuse API keys
4. Save keys into `.env`:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

5. Restart backend only:

```powershell
docker compose --env-file ./.env -f infra/docker/docker-compose.prodtest.yml --project-name reapo-prodtest up -d --force-recreate backend
```

## 6) Common failures and fixes

- `POSTGRES_PASSWORD is missing`
  - Add `POSTGRES_PASSWORD` to `.env`.

- `github_app_not_configured` with missing private key file
  - Ensure `GITHUB_APP_PRIVATE_KEY_PATH` points to `/workspace/<keyfile>` and file exists at repo root.

- Langfuse unhealthy while logs show app started
  - Use the latest compose file in this repo (healthcheck already fixed to container IP).

## 7) Useful commands

```powershell
# Follow logs
docker compose --env-file ./.env -f infra/docker/docker-compose.prodtest.yml --project-name reapo-prodtest logs -f --tail 200

# Restart all
docker compose --env-file ./.env -f infra/docker/docker-compose.prodtest.yml --project-name reapo-prodtest down
docker compose --env-file ./.env -f infra/docker/docker-compose.prodtest.yml --project-name reapo-prodtest up -d

# Destroy including volumes (fresh reset)
docker compose --env-file ./.env -f infra/docker/docker-compose.prodtest.yml --project-name reapo-prodtest down -v
```
