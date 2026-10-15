#!/usr/bin/env bash
# Build image and start the NEW portal (run on the server host).
#
#   TOPO_SSH_PUBLIC_KEY='ssh-ed25519 AAAA…' ./run-docker.sh
#   TOPO_SSH_PASSWORD='…' ./run-docker.sh
#
# Requires deploy/install/topo-ng-latest.tar.gz (from package-release.sh / bundle-for-server.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# shellcheck source=deploy-guard.sh
source "$ROOT/deploy-guard.sh"
topo_ng_refuse_legacy "$@"
topo_ng_require_deploy_ack "$0"

INSTALL="$ROOT/install/topo-ng-latest.tar.gz"
if [[ ! -f "$INSTALL" ]]; then
  echo "error: missing $INSTALL" >&2
  echo "  On dev machine: ./package-release.sh && ./deploy/bundle-for-server.sh" >&2
  exit 1
fi

if [[ -z "${TOPO_SSH_PUBLIC_KEY:-}" && -z "${TOPO_SSH_PASSWORD:-}" ]]; then
  echo "error: set TOPO_SSH_PUBLIC_KEY or TOPO_SSH_PASSWORD so the agent can SSH into the container" >&2
  exit 1
fi

export TOPO_NG_WEB_PORT="${TOPO_NG_WEB_PORT:-8776}"
export TOPO_NG_SSH_PORT="${TOPO_NG_SSH_PORT:-9122}"
export TOPO_NG_BIND="${TOPO_NG_BIND:-0.0.0.0}"
topo_ng_refuse_legacy_port "$TOPO_NG_WEB_PORT" "web"
topo_ng_refuse_legacy_port "$TOPO_NG_SSH_PORT" "ssh"

SECRETS="$ROOT/secrets/install.env"
if [[ ! -f "$SECRETS" ]]; then
  echo "==> generating secrets/install.env on this machine (not from the repo)"
  python3 "$ROOT/gen-secrets.py" --db-host db --write "$SECRETS"
fi

chmod +x "$ROOT/entrypoint-api.sh" "$ROOT/update.sh" "$ROOT/run-docker.sh" 2>/dev/null || true

export COMPOSE_PROJECT_NAME=topo-ng

compose() {
  if docker compose version &>/dev/null; then
    docker compose -p topo-ng "$@"
  elif command -v docker-compose &>/dev/null; then
    docker-compose -p topo-ng "$@"
  else
    echo "error: need 'docker compose' (plugin) or 'docker-compose'" >&2
    exit 1
  fi
}

if [[ "${TOPO_SKIP_BUILD:-0}" == "1" ]]; then
  for img in topo-ng-api:local topo-ng-web:local topo-ng-db:local; do
    if ! docker image inspect "$img" &>/dev/null; then
      echo "error: TOPO_SKIP_BUILD=1 but image $img not found" >&2
      echo "  load first: ./import-docker-image.sh ~/topo-ng-image.tar.gz" >&2
      exit 1
    fi
  done
  echo "skip build (TOPO_SKIP_BUILD=1)"
else
  compose build
fi

# Recreate processes; named volumes (including Postgres) stay. Never pass -v.
# --no-build: offline servers must not hit Docker Hub.
if [[ "${TOPO_SKIP_BUILD:-0}" == "1" ]]; then
  compose up -d --no-build
else
  compose up -d
fi

echo ""
echo "NEW portal only — old site on 8766 / 9022 / topo-portal was not touched."
echo "Web:  http://<host>:${TOPO_NG_WEB_PORT}/"
echo "SSH:  ssh -p ${TOPO_NG_SSH_PORT} topo@<host>"
echo "Logs: docker logs -f topo-ng-api"
