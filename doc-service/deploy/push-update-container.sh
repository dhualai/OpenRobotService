#!/usr/bin/env bash
# Dev machine → NEW portal API container SSH (default 9122): code only.
# Never points at the live legacy stack (9022 / topo-portal / 8766).
#
#   TOPO_NG_DEPLOY=1 ./deploy/push-update-container.sh topo@host
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=deploy-guard.sh
source "$ROOT/deploy/deploy-guard.sh"
topo_ng_refuse_legacy "$@"
topo_ng_require_deploy_ack "$0"

REMOTE="${1:?usage: TOPO_NG_DEPLOY=1 $0 topo@host [--ssh-port 9122]}"
shift || true
# shellcheck source=container-ssh.sh
source "$ROOT/deploy/container-ssh.sh"
topo_ng_ssh_init
SSH_PORT="$TOPO_NG_SSH_PORT"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh-port)
      SSH_PORT="$2"
      shift 2
      ;;
    *)
      echo "unknown: $1" >&2
      exit 1
      ;;
  esac
done

topo_ng_refuse_legacy_port "$SSH_PORT" "ssh"
topo_ng_refuse_legacy "$REMOTE" "$SSH_PORT"

echo "==> package"
./package-release.sh

TAR="dist/topo-ng-latest.tar.gz"
REMOTE_TAR="/tmp/topo-ng-latest.tar.gz"

echo "==> scp to NEW container (${REMOTE} port ${SSH_PORT})"
scp "${TOPO_NG_SSH_OPTS[@]}" -P "$SSH_PORT" "$TAR" "${REMOTE}:${REMOTE_TAR}"

echo "==> update inside container (code + www only; Postgres volume is not in this path)"
ssh "${TOPO_NG_SSH_OPTS[@]}" -p "$SSH_PORT" "$REMOTE" bash -s <<'EOF'
set -euo pipefail
TMPU=$(mktemp -d)
trap 'rm -rf "$TMPU"' EXIT
tar xzf /tmp/topo-ng-latest.tar.gz -C "$TMPU" --strip-components=1
install -m 755 "$TMPU/bin/update.sh" /app/bin/update.sh
/app/bin/update.sh /tmp/topo-ng-latest.tar.gz
rm -f /tmp/topo-ng-latest.tar.gz
# Restart API only. Do not touch sshd or postgres.
pkill -u topo -f 'uvicorn app.main:app' || true
echo "signaled uvicorn; entrypoint will restart it"
EOF

echo "OK: NEW portal code updated (${REMOTE}:${SSH_PORT}). Old site was not contacted."
