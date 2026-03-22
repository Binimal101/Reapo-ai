#!/usr/bin/env bash
# Build and start the stack from repo root. Optional: ./run.sh remote
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

MODE="${1:-local}"
case "$MODE" in
  local)
    COMPOSE_FILE="infra/docker/docker-compose.local.yml"
    PROJECT_NAME="reapo-local"
    ;;
  remote)
    COMPOSE_FILE="infra/docker/docker-compose.remote.yml"
    PROJECT_NAME="reapo-remote"
    ;;
  *)
    echo "Usage: $0 [local|remote]  (default: local)" >&2
    exit 1
    ;;
esac

if [[ -f "$ROOT/.env" ]]; then
  exec docker compose --env-file "$ROOT/.env" -f "$COMPOSE_FILE" --project-name "$PROJECT_NAME" up -d --build
fi

exec docker compose -f "$COMPOSE_FILE" --project-name "$PROJECT_NAME" up -d --build
