#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
image=${FISHBOT_IMAGE:-fishbot/qq-ai-saas:0.1.0}
docker compose version >/dev/null
docker image inspect "$image" >/dev/null
# A fresh directory is not a fresh named volume. Never take over an existing
# Compose deployment before its original configuration has been restored.
if [ ! -f .env.docker ]; then
  project=${COMPOSE_PROJECT_NAME:-fishbot-saas}
  containers=$(docker ps -aq --filter "label=com.docker.compose.project=$project")
  volumes=$(docker volume ls -q --filter "label=com.docker.compose.project=$project")
  if [ -n "$containers" ] || [ -n "$volumes" ] || docker volume inspect "${project}_fishbot-data" >/dev/null 2>&1; then
    printf '%s\n' 'Existing deployment or data volume found; restore the original deployment directory/configuration instead of initializing a new one.' >&2
    exit 1
  fi
fi
docker run --rm --user "$(id -u):$(id -g)" --entrypoint node -v "$PWD:/export" "$image" scripts/docker-init.mjs /export "${1:-admin@example.com}" "${2:-http://127.0.0.1:8787}"
docker compose --env-file .env.docker up -d
printf '%s\n' 'Started. View status: docker compose --env-file .env.docker ps' 'Initial password: .local/docker-secrets/admin-password'
