# Remote Docker Compose .env Guide (Droplet + Langfuse Re-setup)

This guide is for running the same stack on a remote droplet with Docker Compose.

## 1) Recommended directory layout on droplet

- /opt/personal_brand/
  - Reapo-ai/
    - .env (optional — `docker-compose.remote.yml` has defaults)
    - infra/docker/docker-compose.remote.yml

Optional repo-level `.env` at `/opt/personal_brand/Reapo-ai/.env` overrides compose defaults.

## 2) Env overrides (optional)

`docker-compose.remote.yml` ships with working defaults (Postgres password, NextAuth secret/salt, webhook secret). Add or edit `.env` only when you need to override (OpenAI key, GitHub App, public Langfuse URL, Langfuse API keys).

Public Langfuse URL (when not using an SSH tunnel):

```env
LANGFUSE_NEXTAUTH_URL=https://your-domain-or-public-url
```

Backend still uses `LANGFUSE_HOST=http://langfuse:3000` inside Docker.

## 3) Start stack on droplet

From `/opt/personal_brand/Reapo-ai`:

```bash
docker compose -f infra/docker/docker-compose.remote.yml --project-name reapo-remote up -d --build
```

## 4) Verify from terminal

```bash
docker compose -f infra/docker/docker-compose.remote.yml --project-name reapo-remote ps
curl -fsS http://localhost:3000/api/public/health
curl -fsS http://localhost:8090/auth/github/status
```

## 5) Langfuse bootstrap without GUI on droplet

Bootstrap is done from your local browser through SSH tunnel.

From local machine:

```bash
ssh -N -L 3000:127.0.0.1:3000 user@droplet-ip
```

Then open locally:

- `http://localhost:3000`

Complete Langfuse first-user/project setup and generate project API keys.

## 6) Store new Langfuse keys and refresh backend

On droplet, update `/opt/personal_brand/Reapo-ai/.env`:

```env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Then recreate backend:

```bash
docker compose -f infra/docker/docker-compose.remote.yml --project-name reapo-remote up -d --force-recreate backend
```

## 7) Fresh Postgres implications

If postgres volume is new/empty:

- Langfuse has no users/projects/keys yet.
- You must repeat bootstrap once.
- Keep volume/backups to avoid repeated re-bootstrap.

## 8) Backup/restore basics

```bash
# Backup
mkdir -p /opt/backups

docker exec -t reapo-remote-postgres-1 pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > /opt/backups/langfuse_$(date +%F).sql

# Restore example (danger: overwrites db state)
cat /opt/backups/langfuse_YYYY-MM-DD.sql | docker exec -i reapo-remote-postgres-1 psql -U "$POSTGRES_USER" "$POSTGRES_DB"
```

## 9) Troubleshooting

- `github_app_not_configured` and missing key file:
  - Confirm key file exists in repo root and env path is `/workspace/<pem-file>`.

- Langfuse unhealthy:
  - Pull latest repo changes with compose healthcheck fix, then recreate langfuse.

- Wrong callback URL behavior:
  - Ensure `LANGFUSE_NEXTAUTH_URL` is the public URL users actually access.
